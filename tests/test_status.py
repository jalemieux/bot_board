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
from app import db, main, routines, status


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
        # a workspace none of whose bots is up
        self.client.post('/api/workspaces/access', json={'key': 'k'}, headers=self.human)
        self.client.post('/api/workspaces', json={'slug': 'game', 'name': 'Game', 'purpose': 'A game'}, headers=self.human)
        self.client.post('/api/agents', json={'handle': 'jane', 'kind': 'claude-code', 'workspace': 'game'})
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
        self.assertIn('workspace game: none of its bots is up (@jane)', texts)
        self.assertIn('workspace admin has no bot', texts)
        self.assertIn('routine nightly failed: check exited 2', texts)
        self.assertEqual({a['href'] for a in st['attention'] if a['where'] == 'fleet'}, {'/fleet#box-box', '/fleet#bot-tarzan'})
        self.assertEqual(got['workspaces']['href'], '/workspaces/game')
        self.assertTrue(got['routines']['href'].startswith('/routines#r='))
        page = self.client.get('/agents').text
        self.assertIn('need a human', page)
        self.assertIn('href="/workspaces/game"', page)
        self.assertIn('id="box-box"', self.client.get('/fleet').text)
        self.assertIn('id="bot-tarzan"', self.client.get('/fleet').text)

    def test_the_same_trouble_on_many_bots_is_one_item(self):
        why = 'not carried out: @billy is not an operator of this box (KEEPER_OPERATORS)'
        hosts = [{'name': n, 'reporting': True, 'reported_at': db.now(), 'credentials': [],
                  'bots': [{'handle': h, 'harness': 'claude', 'status': 'stopped', 'want': 'stopped', 'problem': why}
                           for h in hs]} for n, hs in (('a', ('anya', 'dara')), ('b', ('amos',)))]
        attention = []
        status._fleet(hosts, attention)
        self.assertEqual(len(attention), 1)
        self.assertEqual(attention[0]['text'], f'3 bots (@anya, @dara, @amos) on a, b: {why}')
        self.assertEqual(attention[0]['href'], '/fleet#fleet-attention')

    def test_usage_alert_groups_bots_even_with_healthy_login(self):
        bots = [{'handle': name, 'harness': 'claude', 'status': 'waiting', 'pool': True,
                 'health': "out of claude usage: weekly limit; resets Oct 19 at 6am"}
                for name in ('tarzan', 'other')]
        self.report(bots, [{'name': 'claude', 'kind': 'login', 'state': 'ok'}])
        st = self.status()
        self.assertFalse(st['ok'])
        alerts = [a for a in st['attention'] if a['where'] == 'fleet']
        self.assertEqual(len(alerts), 1)
        self.assertIn('2 bot(s) blocked', alerts[0]['text'])
        self.assertIn('resets Oct 19', alerts[0]['text'])
        self.assertEqual(st['fleet'][0]['up'], 0)
        for path in ('/admin', '/fleet'):
            page = self.client.get(path).text
            self.assertIn('weekly limit', page)
            self.assertIn('bell-count', page)
        self.assertIn('bell-count', self.client.get('/').text)

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
