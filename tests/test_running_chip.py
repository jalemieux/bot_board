"""#232: the header's chip counts the bots the fleet reports running, not every recent token."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, fleet, main, web


class RunningChip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.register('jac', 'human')
        self.keeper = self.register('keeper-box', 'keeper')
        self.register('routines', 'cron')
        for h in ('ann', 'bob', 'cal', 'dot', 'eve'):
            self.register(h, 'claude-code')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def seen(self, handles, at):
        marks = ','.join('?' * len(handles))
        db.conn().execute(f'UPDATE agents SET last_seen_at=? WHERE handle IN ({marks})', (at, *handles))
        db.conn().commit()

    def report(self):
        r = self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={'bots': [
            {'handle': 'ann', 'harness': 'claude', 'status': 'waiting'},
            {'handle': 'bob', 'harness': 'claude', 'status': 'running 7756'},
            {'handle': 'cal', 'harness': 'claude', 'status': 'stopped'},
            {'handle': 'dot', 'harness': 'claude', 'status': 'waiting', 'health': 'logged out since 00:00'},
        ]})
        self.assertEqual(r.status_code, 200, r.text)

    def test_counts_running_bots_not_keepers_runner_humans_or_stray_tokens(self):
        self.report()
        # everyone made a request just now: cal's token was used by hand, though its launcher is stopped
        self.seen(['jac', 'keeper-box', 'routines', 'ann', 'bob', 'cal', 'dot'], db.now())
        self.assertEqual(db.stats()['active'], 7)       # presence itself is unchanged
        self.assertEqual(fleet.running(), {'bots': 2, 'working': 1, 'keepers': 1, 'runners': 1, 'humans': 1})

    def test_a_bot_no_keeper_reports_counts_by_presence(self):
        self.report()
        self.seen(['eve'], db.now())
        self.assertEqual(fleet.running()['bots'], 3)
        self.seen(['eve'], '2026-01-01T00:00:00+00:00')
        self.assertEqual(fleet.running()['bots'], 2)

    def test_header_shows_running_with_breakdown(self):
        self.report()
        self.seen(['jac', 'keeper-box', 'routines'], db.now())
        page = self.client.get('/').text
        self.assertIn('id="nag">2 running</a>', page)
        self.assertIn('title="2 bots running (1 working) · 1 keeper · 1 runner · 1 human"', page)
        self.assertNotIn(' active</a>', page)
        self.assertEqual(web.running_chip(None), '')


if __name__ == '__main__':
    unittest.main()
