"""#92: the system status on /admin, and GET /api/status. Run with python -m unittest discover -s tests."""
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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register('jac', 'human')
        self.keeper = self.register('keeper-box', 'keeper')
        self.register('tarzan', 'claude-code', workspace='admin')     # the admin workspace jac got has its bot

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind, **more):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind, **more})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def report(self, bots, credentials=()):
        r = self.client.put('/api/fleet/hosts/box', headers=self.keeper,
                            json={'bots': bots, 'credentials': list(credentials),
                                  'update': {'revision': 'v1.0.0', 'result': 'up to date'}})     # a keeper since #107
        self.assertEqual(r.status_code, 200, r.text)

    def status(self):
        r = self.client.get('/api/status')
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class Status(Base):
    def test_all_ok(self):
        self.report([{'handle': 'tarzan', 'harness': 'claude', 'status': 'waiting', 'pool': True}],
                    [{'name': 'claude', 'kind': 'login', 'state': 'ok'}])
        st = self.status()
        self.assertTrue(st['ok'], st['attention'])
        self.assertEqual(st['fleet'][0]['up'], 1)
        page = self.client.get('/admin').text
        self.assertIn('All OK.', page)
        for old, new in (('/agents', '/fleet'), ('/status', '/admin')):     # the roster page is gone (#202)
            r = self.client.get(old, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers['location']), (301, new))

    def test_what_needs_a_human_links_to_where_it_is_fixed(self):
        self.report([{'handle': 'tarzan', 'harness': 'claude', 'status': 'waiting', 'pool': True,
                      'health': 'last run failed (exit 1): boom'}],
                    [{'name': 'claude', 'kind': 'login', 'state': 'missing', 'detail': 'not logged in', 'held': 2}])
        # a workspace none of whose bots is up
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
        self.assertIn('routine nightly failed: check exited 2', texts)
        self.assertEqual({a['href'] for a in st['attention'] if a['where'] == 'fleet'}, {'/fleet#box-box', '/fleet#bot-tarzan'})
        self.assertEqual(got['workspaces']['href'], '/workspaces/game')
        self.assertTrue(got['routines']['href'].startswith('/routines#r='))
        page = self.client.get('/admin').text
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
                      'health': 'last run failed'},
                     {'handle': 'tarzan', 'harness': 'claude', 'status': 'waiting', 'pool': True}])
        self.assertTrue(self.status()['ok'])


if __name__ == '__main__':
    unittest.main()


class Dismissals(Base):
    def setUp(self):
        super().setUp()
        r = self.client.post('/api/workspaces', headers=self.human, json={'slug': 'game', 'name': 'Game', 'purpose': ''})
        self.assertEqual(r.status_code, 201, r.text)

    def alert(self, st=None):
        st = st or self.status()
        return next(a for a in st['attention'] + st['dismissed'] if 'workspace game' in a['text'])

    def test_a_human_dismisses_until_the_cause_is_gone_and_it_can_come_back(self):
        a = self.alert()
        self.assertIn(a, self.status()['attention'])
        bot = self.register('ada', 'claude-code')
        self.assertEqual(self.client.post('/api/status/dismissed', headers=bot, json={'key': a['key']}).status_code, 403)
        self.assertEqual(self.client.post('/api/status/dismissed', headers=self.human, json={'key': 'nope'}).status_code, 404)
        r = self.client.post('/api/status/dismissed', headers=self.human, json={'key': a['key']})
        self.assertEqual(r.status_code, 200, r.text)
        st = self.status()
        self.assertNotIn(a['key'], [x['key'] for x in st['attention']])
        self.assertEqual([(x['key'], x['dismissed_by']) for x in st['dismissed']], [(a['key'], 'jac')])
        self.assertNotIn('workspace game', self.client.get('/admin').text, 'the status page leaves it out too')
        # restore
        st = self.client.delete(f"/api/status/dismissed/{a['key']}", headers=self.human).json()
        self.assertIn(a['key'], [x['key'] for x in st['attention']])
        # dismissed again, then the cause is fixed: the dismissal is forgotten, so a recurrence shows
        self.client.post('/api/status/dismissed', headers=self.human, json={'key': a['key']})
        c = db.conn()
        c.execute("UPDATE workspaces SET archived=1 WHERE slug='game'"); c.commit()
        self.assertEqual(self.status()['dismissed'], [])
        c.execute("UPDATE workspaces SET archived=0 WHERE slug='game'"); c.commit()
        self.assertIn(a['key'], [x['key'] for x in self.status()['attention']])

    def test_the_key_ignores_counts_and_ages(self):
        self.assertEqual(status.key_of('box is not reporting: last heard 5 min ago', '/fleet'),
                         status.key_of('box is not reporting: last heard 12 min ago', '/fleet'))
        self.assertNotEqual(status.key_of('workspace a has no bot', '/workspaces/a'),
                            status.key_of('workspace a: none of its bots is up', '/workspaces/a'))
