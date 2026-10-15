"""Killing a stuck bot from /fleet: the request, the page, and what the keeper does with it.
Run with python -m unittest discover -s tests."""
import importlib.machinery
import importlib.util
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from app import db, main


def load_keeper(home):
    """bin/keeper, with its ~/.config/bot_board under a scratch home rather than the real one."""
    os.environ['HOME'] = home
    os.environ['BOT_CONFIG'] = os.path.join(home, 'config')
    loader = importlib.machinery.SourceFileLoader('keeper', str(ROOT / 'bin' / 'keeper'))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader('keeper', loader))
    loader.exec_module(mod)
    return mod


class Kill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register('op', 'human')
        self.keeper = self.register('keeper-box', 'keeper')
        self.report('running proj')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def report(self, status):
        r = self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={'bots': [
            {'handle': 'tarzan', 'harness': 'claude', 'status': status, 'serves': '/pool', 'pool': True}]})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['want']

    def ask(self, expected=200, **body):
        r = self.client.put('/api/fleet/bots/tarzan', headers=self.human, json={'host': 'box', **body})
        self.assertEqual(r.status_code, expected, r.text)

    def test_kill_reaches_the_keeper_and_the_page(self):
        self.ask(run=False, kill=True)
        want = self.report('running proj, stopping')
        self.assertFalse(want[0]['run'])
        self.assertTrue(want[0]['kill'])
        page = self.client.get('/fleet').text
        self.assertIn('kill asked', page)
        self.assertIn('kill asked by @op', page)
        self.assertIn('data-kill="1"', page)

    def test_a_later_request_clears_the_kill(self):
        self.ask(run=False, kill=True)
        self.ask(run=False)
        self.assertIsNone(self.report('running proj')[0]['kill'])

    def test_a_start_that_never_comes_can_be_withdrawn(self):
        self.report('stopped')
        self.ask(run=True)          # and the keeper never answers: no problem is ever set
        page = self.client.get('/fleet').text
        self.assertNotIn('data-run="1"', page)     # no second start
        self.assertIn('data-run="0" title="withdraw the start">cancel</button>', page)
        self.ask(run=False)
        self.assertFalse(self.report('stopped')[0]['run'])

    def test_kill_with_run_is_refused(self):
        self.ask(run=True, kill=True, expected=409)

    def test_only_a_human_kills(self):
        r = self.client.put('/api/fleet/bots/tarzan', headers=self.keeper, json={'host': 'box', 'run': False, 'kill': True})
        self.assertEqual(r.status_code, 403)

    def test_keeper_kills_once_even_when_a_stop_is_pending(self):
        home = os.environ.get('HOME'), os.environ.get('BOT_CONFIG')
        try:
            keeper = load_keeper(self.tmp.name)
        finally:
            for k, v in zip(('HOME', 'BOT_CONFIG'), home):
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
        self.assertTrue(keeper.CFG.startswith(self.tmp.name))
        hands = keeper.Hands()
        spawned = []
        hands.spawn = lambda h, cmd: spawned.append(cmd[1:])
        seen = [{'handle': 'tarzan', 'status': 'running proj, stopping'}]
        want = [{'handle': 'tarzan', 'harness': 'claude', 'run': False, 'kill': '2026-10-15T01:00:00Z', 'set_by': 'op',
                 'set_at': '2026-10-15T01:00:00Z', 'move_to': None, 'fetch': None}]
        hands.reconcile(want, seen)
        hands.reconcile(want, seen)     # the next report, before the kill has landed
        self.assertEqual(spawned, [['kill', 'tarzan']])
        # a plain stop while it is already stopping does nothing new
        hands.reconcile([{**want[0], 'kill': None}], seen)
        self.assertEqual(spawned, [['kill', 'tarzan']])


if __name__ == '__main__':
    unittest.main()
