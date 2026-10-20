"""#181: with no BOT_BOARD_PROJECT_ADMIN_KEY, the first human runs the board, gets the admin workspace and grants others."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main


class AdminGrants(unittest.TestCase):
    key = ''

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.saved_key, main.PROJECT_ADMIN_KEY = main.PROJECT_ADMIN_KEY, self.key
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        main.PROJECT_ADMIN_KEY = self.saved_key
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind='human'):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def call(self, method, path, auth, expected=200, json=None):
        r = self.client.request(method, path, headers=auth, json=json)
        self.assertEqual(r.status_code, expected, r.text)
        return r.json()

    def can_create(self, auth):
        return self.call('GET', '/api/workspaces', auth)['can_create']


class NoKey(AdminGrants):
    def test_first_human_manages_and_grants_the_next(self):
        bot = self.register('ada', 'claude-code')     # bots never become admins
        jac = self.register('jac')
        bea = self.register('bea')
        self.assertTrue(self.can_create(jac))
        self.assertFalse(self.can_create(bea))
        self.call('POST', '/api/workspaces/access', bot, 403, json={})
        self.assertEqual(self.call('GET', '/api/workspaces/admin', jac)['workspace']['slug'], 'admin')   # a bot has somewhere to go
        self.assertEqual(self.call('GET', '/api/workspaces/access', bea)['how'], 'ask')
        self.call('POST', '/api/workspaces/access', bea, 403, json={})
        self.call('PUT', '/api/workspaces/admins/bea', bea, 403)      # only an admin grants
        got = self.call('PUT', '/api/workspaces/admins/bea', jac)['admins']
        self.assertEqual([a['handle'] for a in got], ['jac', 'bea'])
        self.assertTrue(self.can_create(bea))
        self.call('PUT', '/api/workspaces/admins/ada', jac, 403)      # not a human
        self.call('DELETE', '/api/workspaces/admins/jac', bea)
        self.assertFalse(self.can_create(jac))
        self.call('DELETE', '/api/workspaces/admins/bea', bea, 409)   # never the last one

    def test_a_board_whose_humans_came_first_can_be_claimed_once(self):
        jac = self.register('jac')
        db.conn().execute('DELETE FROM board_admins')
        db.conn().commit()
        self.assertEqual(self.call('GET', '/api/workspaces/access', jac)['how'], 'claim')
        self.call('POST', '/api/workspaces/access', jac, json={})
        self.assertTrue(self.can_create(jac))
        bea = self.register('bea')
        self.call('POST', '/api/workspaces/access', bea, 403, json={})

    def test_box_operators_only_once_a_keeper_names_them(self):
        keeper = self.register('keeper-box', 'keeper')
        self.call('PUT', '/api/fleet/hosts/box', keeper, json={'bots': [], 'operators': ['jac']})
        stranger = self.register('mallory')
        self.assertFalse(self.can_create(stranger))
        self.assertEqual(self.call('GET', '/api/workspaces/access', stranger)['how'], 'ask')
        self.assertTrue(self.can_create(self.register('jac')))


class WithKey(AdminGrants):
    key = 'k'

    def test_the_key_still_unlocks_and_nobody_is_granted_by_arriving(self):
        jac = self.register('jac')
        self.assertFalse(self.can_create(jac))
        self.assertEqual(self.call('GET', '/api/workspaces/access', jac)['how'], 'key')
        self.call('POST', '/api/workspaces/access', jac, 403, json={'key': 'nope'})
        self.call('POST', '/api/workspaces/access', jac, json={'key': 'k'})
        self.assertTrue(self.can_create(jac))


del AdminGrants

if __name__ == '__main__':
    unittest.main()
