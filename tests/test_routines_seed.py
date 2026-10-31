"""The routines the board ships (docs/routines/) come with the board: stored once, paused, in
the admin workspace, when that workspace exists; a human says yes before one runs, and
deleting one sticks. Run with python -m unittest discover -s tests."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main, registry, routines


class SeededRoutines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def call(self, method, path, data=None, auth=None, expected=200):
        return self.text(method, path, data, auth, expected, json=True)

    def text(self, method, path, data=None, auth=None, expected=200, json=False):
        r = self.client.request(method, path, json=data, headers=auth)
        self.assertEqual(r.status_code, expected, r.text)
        return r.json() if json else r.text

    def routines(self):
        return {r['name']: r for r in self.call('GET', '/api/routines')['routines']}

    def restart(self):
        self.client.__exit__(None, None, None)
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def test_the_shipped_routine_is_stored_paused_once_the_admin_workspace_exists(self):
        self.assertEqual(self.routines(), {}, "no admin workspace yet (no human): nothing is seeded")
        human = self.register('jac', 'human')          # the first human brings the admin workspace, and with it the chores
        rs = self.routines()
        self.assertIn('board-daily-care', rs)
        care = rs['board-daily-care']
        self.assertEqual((care['state'], care['channel'], care['version']), ('paused', 'admin', 1))
        self.assertIsNone(care.get('approved_by'))
        full = self.call('GET', f"/api/routines/{care['id']}")
        self.assertTrue(full['check'].startswith('#!/usr/bin/env python3'))
        self.assertIn('board-care', full['prompt'])
        self.assertEqual(full['skill'], 'board-care')
        self.assertEqual(full['every'], 600)
        # the board asks for the yes itself, as for any routine nobody approved yet
        asks = [a['text'] for a in self.call('GET', '/api/status', auth=human)['attention']]
        self.assertTrue(any('board-daily-care' in a and 'waits for your yes' in a for a in asks), asks)
        # the skill the prompt names is on the board too
        self.assertIn('Do or propose', self.text('GET', '/api/skills/board-care'))

    def test_seeding_happens_once_so_a_deleted_routine_stays_deleted(self):
        human = self.register('jac', 'human')
        rid = self.routines()['board-daily-care']['id']
        self.assertEqual(routines.seed(), [], "already stored: a second seed stores nothing")
        self.call('DELETE', f'/api/routines/{rid}', auth=human)
        self.assertEqual(routines.seed(), [])
        self.restart()
        self.assertNotIn('board-daily-care', self.routines(), "deleted stays deleted across restarts")

    def test_a_board_from_before_the_routine_shipped_gets_it_at_the_next_start(self):
        self.register('jac', 'human')
        rid = self.routines()['board-daily-care']['id']
        # make the board look like one seeded before docs/routines existed: no marker, no row
        with db._write_lock, db.conn() as c:
            c.execute("DELETE FROM routines WHERE id=?", (rid,))
            c.execute("DELETE FROM routines_meta WHERE key='seeded:board-daily-care'")
        self.restart()
        self.assertIn('board-daily-care', self.routines())

    def test_a_skill_shipped_after_the_first_start_is_seeded_and_a_deleted_one_stays_deleted(self):
        human = self.register('jac', 'human')
        self.text('GET', '/api/skills/board-care')
        # a board seeded before board-care shipped: its once-flag is set and the skill has no revision at all
        with db._write_lock, db.conn() as c:
            c.execute("DELETE FROM skills WHERE name='board-care'")
        self.text('GET', '/api/skills/board-care', expected=404)
        registry.seed()
        self.text('GET', '/api/skills/board-care')
        # deleted by a human after it was seeded: the tombstone revision keeps it away
        rev = registry.current_revision('board-care')
        registry.delete_skill('board-care', rev, 'not wanted here', None)
        registry.seed()
        self.text('GET', '/api/skills/board-care', expected=404)


if __name__ == '__main__':
    unittest.main()
