"""Agents page: sorting the roster into roles. Run with python -m unittest discover -s tests."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main, web

HOSTS = {'jacs-m5', 'minipc-1'}
CHANNELS = {'space_game_1', 'bot_board', 'minecraft_java'}


def agent(handle, kind='claude-code', description=''):
    return {'handle': handle, 'kind': kind, 'description': description}


class Classify(unittest.TestCase):
    def test_session_handle_gives_box_and_project(self):
        c = web.classify_agent(agent('jacs-m5.local-space_game_1-4935', description='space_game_1: art issues'),
                               HOSTS, CHANNELS)
        self.assertEqual((c['role'], c['box'], c['project'], c['what']), ('session', 'jacs-m5', 'space_game_1', 'art issues'))

    def test_hyphenated_host(self):
        c = web.classify_agent(agent('minipc-1-bot_board-d72a'), HOSTS, CHANNELS)
        self.assertEqual((c['box'], c['project']), ('minipc-1', 'bot_board'))

    def test_truncated_project_maps_to_its_channel(self):
        c = web.classify_agent(agent('jacs-m5.local-minecraft_jav-16cb'), HOSTS, CHANNELS)
        self.assertEqual(c['project'], 'minecraft_java')

    def test_unknown_host(self):
        c = web.classify_agent(agent('macbook-pro-2-wordsnap-aa11'), HOSTS, CHANNELS)
        self.assertEqual((c['box'], c['project']), ('macbook-pro-2', 'wordsnap'))

    def test_pool_bots(self):
        named = web.classify_agent(agent('alma', 'codex', 'standby pool on minipc-1: codex on any project'), HOSTS, CHANNELS)
        self.assertEqual((named['role'], named['box']), ('pool', 'minipc-1'))
        seeded = web.classify_agent(agent('minipc-1-pool-6ff3'), HOSTS, CHANNELS)
        self.assertEqual((seeded['role'], seeded['box']), ('pool', 'minipc-1'))

    def test_people(self):
        self.assertEqual(web.classify_agent(agent('jac', 'human'), HOSTS, CHANNELS)['role'], 'people')
        self.assertEqual(web.classify_agent(agent('keeper-minipc-1', 'keeper'), HOSTS, CHANNELS)['role'], 'people')


class Page(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_sections(self):
        for handle, kind, desc in [('minipc-1-bot_board-ab12', 'claude-code', 'bot_board: fixing the roster'),
                                   ('alma', 'codex', 'standby pool on minipc-1: codex'),
                                   ('jac', 'human', 'a person')]:
            r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind, 'description': desc})
            self.assertEqual(r.status_code, 201, r.text)
            self.client.get('/api/me', headers={'Authorization': 'Bearer ' + r.json()['token']})
        page = self.client.get('/agents').text
        self.assertIn('Working now', page)
        self.assertIn('fixing the roster', page)
        self.assertIn('Pool bots', page)
        self.assertIn('People and keepers', page)
        self.assertNotIn('Ended sessions', page)


if __name__ == '__main__':
    unittest.main()
