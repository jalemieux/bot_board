"""A single-user board: one person, many laptops, any handle, and every human runs the board.
Run with python -m unittest discover -s tests."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main


class SingleUser(unittest.TestCase):
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

    def register(self, handle, kind='human', expected=201, **more):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind, **more})
        self.assertEqual(r.status_code, expected, r.text)
        return r.json()

    def auth(self, token):
        return {'Authorization': 'Bearer ' + token}

    def me(self, token):
        return self.client.get('/api/me', headers=self.auth(token))

    def test_another_laptop_signs_in_to_the_same_human(self):
        first = self.register('jac')
        again = self.register('JAC', expected=200)
        self.assertTrue(again['signed_in'])
        self.assertEqual(again['agent']['handle'], 'jac')
        self.assertNotEqual(again['token'], first['token'])
        for token in (first['token'], again['token']):       # both laptops stay signed in
            self.assertEqual(self.me(token).json()['agent']['handle'], 'jac')
        r = self.client.post('/api/messages', headers=self.auth(again['token']), json={'channel': 'lobby', 'body': 'hi'})
        self.assertEqual(r.json()['message']['author'], 'jac')

    def test_bot_handles_are_never_signed_in_to(self):
        self.register('ada', 'claude-code')
        self.register('ada', 'human', expected=409)
        self.register('jac')
        self.register('jac', 'claude-code', expected=409)

    def test_rotate_signs_every_laptop_out(self):
        first = self.register('jac')['token']
        second = self.register('jac', expected=200)['token']
        fresh = self.client.post('/api/me/rotate', headers=self.auth(second)).json()['token']
        self.assertEqual(self.me(first).status_code, 401)
        self.assertEqual(self.me(second).status_code, 401)
        self.assertEqual(self.me(fresh).status_code, 200)

    def test_the_invite_code_still_gates_signing_in(self):
        self.register('jac')
        saved, main.INVITE_CODE = main.INVITE_CODE, 'open-sesame'
        try:
            self.register('jac', expected=403)
            self.register('jac', expected=200, invite_code='open-sesame')
        finally:
            main.INVITE_CODE = saved

    def test_every_human_runs_the_board(self):
        self.register('jac')
        omg = self.auth(self.register('omg')['token'])
        r = self.client.post('/api/workspaces', headers=omg, json={'slug': 'game', 'name': 'Game', 'purpose': 'A game'})
        self.assertEqual(r.status_code, 201, r.text)
        r = self.client.put('/api/fleet/secrets/GH_TOKEN', headers=omg, json={'value': 'ghp_x'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get('/admin/agents.md', headers=omg).status_code, 200)
        self.assertEqual(self.client.get('/api/workspaces/access', headers=omg).status_code, 404)
        bot = self.auth(self.register('ada', 'claude-code')['token'])
        self.assertEqual(self.client.post('/api/workspaces', headers=bot, json={
            'slug': 'nope', 'name': 'Nope', 'purpose': 'x'}).status_code, 403)

    def test_any_human_starts_a_bot_on_any_box(self):
        omg = self.auth(self.register('omg')['token'])
        keeper = self.auth(self.register('keeper-box', 'keeper')['token'])
        r = self.client.put('/api/fleet/hosts/box', headers=keeper, json={'bots': [], 'operators': ['jac']})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.put('/api/fleet/bots/ada', headers=omg, json={'host': 'box', 'run': True, 'harness': 'claude',
                                                                    'workspace': 'admin'})
        self.assertEqual(r.status_code, 200, r.text)


if __name__ == '__main__':
    unittest.main()
