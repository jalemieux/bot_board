"""Taking a machine off the fleet (#279): the box is forgotten and its keeper and bots are
retired (#280's retire), so a fresh install there reports without a 403."""
import os
import tempfile
import threading
import unittest
from fastapi.testclient import TestClient
from app import db, main, fleet


class RemoveMachine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register('operator', 'human')
        self.keeper = self.register('keeper-box', 'keeper')
        self.client.post('/api/workspaces', headers=self.human, json={'slug': 'ws', 'name': 'WS', 'purpose': 'x'})
        self.bot = self.register('jane', 'codex', 'ws')
        self.report()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'): db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind, workspace=None):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind, 'workspace': workspace})
        self.assertIn(r.status_code, (200, 201), r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def report(self, auth=None, name='box'):
        return self.client.put(f'/api/fleet/hosts/{name}', headers=auth or self.keeper,
                               json={'bots': [{'handle': 'jane', 'harness': 'codex', 'status': 'stopped'}],
                                     'operators': ['operator']})

    def handles(self):
        return {a['handle'] for a in self.client.get('/api/agents', headers=self.human).json()['agents']}

    def test_a_human_removes_a_box_and_its_keeper_and_bots_are_retired(self):
        post = self.client.post('/api/messages', headers=self.bot, json={'channel': 'ws', 'body': 'working on it'})
        self.assertEqual(post.status_code, 201, post.text)
        tid = post.json()['message']['id']
        self.client.post(f'/api/threads/{tid}/worker', headers=self.bot).raise_for_status()
        # still reporting: refused unless the human insists
        r = self.client.delete('/api/fleet/hosts/box', headers=self.human)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn('still reporting', r.json()['error'])
        r = self.client.delete('/api/fleet/hosts/box?force=true', headers=self.human)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted(r.json()['retired']), ['jane', 'keeper-box'])
        # gone from the fleet and the agent lists; their tokens no longer work
        self.assertEqual(self.client.get('/api/fleet', headers=self.human).json()['hosts'], [])
        self.assertNotIn('jane', self.handles())
        self.assertNotIn('keeper-box', self.handles())
        self.assertEqual(self.client.get('/api/me', headers=self.bot).status_code, 401)
        self.assertEqual(self.report().status_code, 401)
        # the thread is up for pickup, and the old message still reads as jane's
        self.assertIsNone(self.client.get(f'/api/threads/{tid}/worker').json().get('worker'))
        msg = self.client.get(f'/api/messages/{tid}', headers=self.human).json()
        self.assertEqual(msg.get('message', msg)['author'], 'jane')
        self.assertEqual(self.client.get('/api/agents/jane').status_code, 404)    # #280: off the agent pages
        # the handle is not given out again, and a fresh keeper takes the box's name
        r = self.client.post('/api/agents', json={'handle': 'jane', 'kind': 'codex'})
        self.assertEqual(r.status_code, 409, r.text)
        fresh = self.register('keeper-box-2', 'keeper')
        self.assertEqual(self.report(fresh).status_code, 200)

    def test_a_box_whose_keeper_stopped_is_removed_without_insisting(self):
        with db.conn() as c:
            c.execute("UPDATE fleet_hosts SET reported_at='2026-10-01T00:00:00+00:00'")
        r = self.client.delete('/api/fleet/hosts/box', headers=self.human)
        self.assertEqual(r.status_code, 200, r.text)

    def test_the_box_s_own_keeper_takes_it_off_on_uninstall(self):
        r = self.client.delete('/api/fleet/hosts/box', headers=self.keeper)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted(r.json()['retired']), ['jane', 'keeper-box'])
        # another keeper, or a bot, cannot
        self.report()
        other = self.register('keeper-other', 'keeper')
        bot = self.register('tarzan', 'codex')
        fresh = self.register('keeper-box-3', 'keeper')
        self.report(fresh)
        for auth in (other, bot):
            self.assertEqual(self.client.delete('/api/fleet/hosts/box', headers=auth).status_code, 403)

    def test_a_bot_moving_to_another_box_is_not_retired_with_this_one(self):
        self.report(self.register('keeper-elsewhere', 'keeper'), 'elsewhere')
        self.assertIsNone(fleet.want('jane', 'elsewhere', True, 'codex', 'operator'))
        r = self.client.delete('/api/fleet/hosts/box?force=true', headers=self.human)
        self.assertEqual(r.json()['retired'], ['keeper-box'])
        self.assertIn('jane', self.handles())


if __name__ == '__main__':
    unittest.main()
