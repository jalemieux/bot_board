"""#92: /agents as the system status page, and GET /api/status. Run with python -m unittest discover -s tests."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main, routines


class Status(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        main.PROJECT_ADMIN_KEY = 'k'
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register('jac', 'human')
        self.keeper = self.register('keeper-box', 'keeper')
        self.register('tarzan', 'claude-code')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def report(self, bots, credentials=()):
        r = self.client.put('/api/fleet/hosts/box', headers=self.keeper,
                            json={'bots': bots, 'credentials': list(credentials)})
        self.assertEqual(r.status_code, 200, r.text)

    def status(self):
        r = self.client.get('/api/status')
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_all_ok(self):
        self.report([{'handle': 'tarzan', 'harness': 'claude', 'status': 'waiting', 'pool': True}],
                    [{'name': 'claude', 'kind': 'login', 'state': 'ok'}])
        st = self.status()
        self.assertTrue(st['ok'], st['attention'])
        self.assertEqual(st['fleet'][0]['up'], 1)
        page = self.client.get('/agents').text
        self.assertIn('All OK.', page)
        self.assertIn('<h1>Status</h1>', page)
        self.assertIn('Who is on the board', page)     # the roster is still there, below
        self.assertEqual(self.client.get('/status').status_code, 200)

    def test_what_needs_a_human_links_to_where_it_is_fixed(self):
        self.report([{'handle': 'tarzan', 'harness': 'claude', 'status': 'waiting', 'pool': True,
                      'health': 'last run failed (exit 1): boom'}],
                    [{'name': 'claude', 'kind': 'login', 'state': 'missing', 'detail': 'not logged in', 'held': 2}])
        # a project with its own pool, none of whose bots is up
        self.client.post('/api/projects/access', json={'key': 'k'}, headers=self.human)
        self.client.post('/api/projects', json={'slug': 'game', 'name': 'Game', 'purpose': 'A game'}, headers=self.human)
        db.conn().execute("INSERT INTO project_pools (project_id, configured, revision) "
                          "SELECT id, 1, 1 FROM projects WHERE slug='game'")
        db.conn().commit()
        # a routine the runner said failed
        now = db.now()
        db.conn().execute("INSERT INTO routines (definition, state, defined_at, failure, created_by, updated_by,"
                          " created_at, updated_at) VALUES (?, 'failed', 0, 'check exited 2', 1, 1, ?, ?)",
                          (json.dumps({'name': 'nightly', 'channel': 'game', 'every': 600}), now, now))
        db.conn().commit()
        st = self.status()
        self.assertFalse(st['ok'])
        got = {a['where']: a for a in st['attention']}
        texts = ' | '.join(a['text'] for a in st['attention'])
        self.assertIn('claude missing on box, 2 bots held', texts)
        self.assertIn('@tarzan on box: last run failed', texts)
        self.assertIn('project game: none of its pool bots is up', texts)
        self.assertIn('routine nightly failed: check exited 2', texts)
        self.assertEqual(got['fleet']['href'], '/fleet#box-box')
        self.assertEqual(got['projects']['href'], '/projects/game')
        self.assertTrue(got['routines']['href'].startswith('/routines#r='))
        page = self.client.get('/agents').text
        self.assertIn('need a human', page)
        self.assertIn('href="/projects/game"', page)
        self.assertIn('id="box-box"', self.client.get('/fleet').text)

    def test_a_box_gone_quiet(self):
        self.report([])
        db.conn().execute("UPDATE fleet_hosts SET reported_at='2026-01-01T00:00:00+00:00'")
        db.conn().commit()
        st = self.status()
        self.assertIn('box is not reporting', st['attention'][0]['text'])

    def test_stopped_bot_nobody_asked_for_is_not_a_problem(self):
        self.report([{'handle': 'old', 'harness': 'claude', 'status': 'stopped', 'pool': True,
                      'health': 'last run failed'}])
        self.assertTrue(self.status()['ok'])


if __name__ == '__main__':
    unittest.main()
