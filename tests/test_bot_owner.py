"""#225: who owns an issue or PR is its bot:<handle> label, which counts while that bot runs
(GET /api/agents/<handle> says `running`); a hand-off stop leaves the bot's work on GitHub.
Run with python -m unittest discover -s tests."""
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from fastapi.testclient import TestClient       # noqa: E402
from app import db, main                         # noqa: E402
from fakes import harness as fh                  # noqa: E402
from fakes.board import FakeBoard                # noqa: E402
from test_launcher_e2e import BOT, Launcher      # noqa: E402
from test_fleet_kill import load_keeper          # noqa: E402


class HandoffStop(unittest.TestCase):
    def run_stop(self, *flag):
        with FakeBoard() as board:
            L = Launcher(board, "claude")
            try:
                L.plan(fh.step("claude", "ok", "rules read"), fh.step("claude", "ok", "pushed, commented"))
                L.start()
                L.wait_for(lambda: L.read("runs") == "1" and "startup catch-up" in L.log(), "the first turn")
                r = subprocess.run([BOT, "stop", "tbot", *flag], env=L.env, capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                L.proc.wait(timeout=15)
                self.assertEqual(L.proc.returncode, 0, L.output())
                return L.prompts(), L.log()
            finally:
                L.close()

    def test_a_handoff_stop_runs_one_last_turn_first(self):
        prompts, log = self.run_stop("--handoff")
        self.assertEqual(len(prompts), 2)
        self.assertIn("push any work in progress", prompts[1])
        self.assertIn("bot:tbot", prompts[1])
        self.assertIn("hand-off turn done", log)

    def test_a_plain_stop_does_not(self):
        prompts, log = self.run_stop()
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("hand-off", log)


class Board(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register('op', 'human')
        self.keeper = self.register('keeper-box', 'keeper')
        self.bot = self.register('tarzan', 'claude-code')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def running(self):
        return self.client.get('/api/agents/tarzan').json()['agent']['running']

    def test_running_is_the_fleets_word_else_presence(self):
        self.client.get('/api/me', headers=self.bot)
        self.assertTrue(self.running(), 'not on the fleet: active by presence')
        self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={'bots': [
            {'handle': 'tarzan', 'harness': 'claude', 'status': 'waiting', 'pool': True}]})
        r = self.client.put('/api/fleet/bots/tarzan', headers=self.human, json={'host': 'box', 'run': False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.running(), 'a human stopped it on /fleet')

    def test_the_keeper_asks_for_a_handoff_stop_only_with_a_handoff(self):
        home = os.environ.get('HOME'), os.environ.get('BOT_CONFIG')
        try:
            keeper = load_keeper(self.tmp.name)
        finally:
            for k, v in zip(('HOME', 'BOT_CONFIG'), home):
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
        seen = [{'handle': 'tarzan', 'status': 'waiting'}]
        base = {'handle': 'tarzan', 'harness': 'claude', 'run': False, 'kill': None, 'set_by': 'op',
                'set_at': '2026-10-21T23:00:00Z', 'move_to': None, 'fetch': None}
        for threads, want in ((None, ['stop', 'tarzan']), ('keep', ['stop', 'tarzan']),
                              ('handoff', ['stop', 'tarzan', '--handoff']), ('retire', ['stop', 'tarzan', '--handoff'])):
            hands = keeper.Hands()
            spawned = []
            hands.spawn = lambda h, cmd: spawned.append(cmd[1:])
            hands.reconcile([{**base, 'threads': threads}], seen)
            self.assertEqual(spawned, [want], threads)


if __name__ == '__main__':
    unittest.main()
