"""Adding a bot from /fleet with a project: the dedication is remembered and applied
once the bot comes online. Run with python -m unittest discover -s tests."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main


class Dedicate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        main.PROJECT_ADMIN_KEY = 'test-management-key'
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register('op', 'human')
        self.keeper = self.register('keeper-box', 'keeper')
        self.call('POST', '/api/projects/access', {'key': main.PROJECT_ADMIN_KEY})
        self.call('POST', '/api/projects', {'slug': 'falco', 'name': 'Falco', 'purpose': 'Bugs',
                  'repositories': [{'name': 'falco', 'url': 'https://github.com/example/falco'}]}, expected=201)
        # A box must be reporting before a bot can be asked for on it.
        self.report(bots=[])

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
        r = self.client.request(method, path, json=data, headers=auth or self.human)
        self.assertEqual(r.status_code, expected, r.text)
        return r.json()

    def report(self, bots):
        r = self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={'bots': bots})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def pool_bots(self):
        return self.call('GET', '/api/projects/falco/bots')['bots']

    def test_project_choice_is_applied_once_the_new_bot_comes_online(self):
        handle = self.call('POST', '/api/fleet/bots',
                           {'host': 'box', 'harness': 'claude', 'project': 'falco'}, expected=200)['handle']
        # Not online yet: nothing is dedicated.
        self.assertEqual(self.pool_bots(), [])

        # The keeper starts it; the bot registers and the box reports it as a pool bot up.
        self.register(handle, 'claude')
        self.report(bots=[{'handle': handle, 'harness': 'claude', 'status': 'waiting', 'pool': True}])

        self.assertEqual(self.pool_bots(), [handle])
        # The pending request is consumed, so a later report does not re-apply it.
        row = db.conn().execute('SELECT project FROM fleet_bots WHERE handle=?', (handle,)).fetchone()
        self.assertIsNone(row['project'])

    def test_no_project_means_no_dedication(self):
        handle = self.call('POST', '/api/fleet/bots', {'host': 'box', 'harness': 'claude'}, expected=200)['handle']
        self.register(handle, 'claude')
        self.report(bots=[{'handle': handle, 'harness': 'claude', 'status': 'waiting', 'pool': True}])
        self.assertEqual(self.pool_bots(), [])

    def test_request_for_a_gone_project_is_dropped_when_the_bot_arrives(self):
        handle = self.call('POST', '/api/fleet/bots',
                           {'host': 'box', 'harness': 'claude', 'project': 'ghost'}, expected=200)['handle']
        self.register(handle, 'claude')
        self.report(bots=[{'handle': handle, 'harness': 'claude', 'status': 'waiting', 'pool': True}])
        row = db.conn().execute('SELECT project FROM fleet_bots WHERE handle=?', (handle,)).fetchone()
        self.assertIsNone(row['project'])


if __name__ == '__main__':
    unittest.main()
