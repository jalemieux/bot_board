"""#238: the keeper reports what runs (its loaded commit and PROTOCOL; per bot behind, restartable,
protocol and session_gen), a launcher below the restart floor is stopped and started at once, and
the board takes the new fields while still taking a report without them.
Run with python -m unittest discover -s tests."""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_fleet_kill import load_keeper     # noqa: E402
from fastapi.testclient import TestClient     # noqa: E402
from app import db, fleet, main     # noqa: E402


def sh(cwd, *cmd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class Keeper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        saved = os.environ.get('HOME'), os.environ.get('BOT_CONFIG')
        try:
            self.k = load_keeper(self.tmp.name)
        finally:
            for k, v in zip(('HOME', 'BOT_CONFIG'), saved):
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
        # a checkout of three commits: old (before the floor), floor, new
        self.src = os.path.join(self.tmp.name, "src")
        os.makedirs(self.src)
        sh(self.src, "git", "init", "-q")
        self.commits = []
        for name in ("old", "floor", "new"):
            Path(self.src, name).write_text(name)
            sh(self.src, "git", "add", name)
            sh(self.src, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)
            self.commits.append(sh(self.src, "git", "rev-parse", "HEAD"))
        self.k.SRC, self.k.RESTART_FLOOR = self.src, self.commits[1]

    def tearDown(self):
        self.tmp.cleanup()

    def test_behind_and_restartable_come_from_git(self):
        old, floor, new = self.commits
        self.assertEqual(self.k.launcher_facts(new[:7], new), (False, True))
        self.assertEqual(self.k.launcher_facts(floor[:7] + "-dirty", new), (True, True))
        self.assertEqual(self.k.launcher_facts(old[:7], new), (True, False))
        self.assertEqual(self.k.launcher_facts("", new), (None, False), "no version file: older than the floor")
        self.assertEqual(self.k.launcher_facts("dev", new), (None, None), "git cannot tell")

    def test_a_launcher_below_the_floor_is_stopped_without_waiting(self):
        hands = self.k.Hands()
        spawned = []
        hands.spawn = lambda h, cmd: spawned.append(cmd[1:])
        state = Path(self.k.CFG) / "bots" / "jonas"
        state.mkdir(parents=True)
        (state / "restart").touch()     # asked just now
        seen = lambda st, ok: [{"handle": "jonas", "harness": "claude", "status": st, "restartable": ok}]
        self.assertEqual(hands.reconcile([], seen("waiting", None)), {}, "unknown: the grace still applies")
        self.assertEqual(spawned, [])
        hands.reconcile([], seen("running 7701", False))
        self.assertEqual(spawned, [], "a run in progress finishes first")
        problems = hands.reconcile([], seen("waiting", False))
        self.assertEqual(spawned, [["stop", "jonas"]])
        self.assertIn("did not restart on its own", problems["jonas"])

    def test_bots_report_what_their_launcher_runs(self):
        state = Path(self.k.CFG) / "bots" / "ann"
        state.mkdir(parents=True)
        (state / "version").write_text(self.commits[0][:7] + "\n")
        (state / "protocol").write_text("1\n")
        (state / "session-gen").write_text("2\n")
        self.k.out = lambda *cmd: "ann\tclaude\twaiting\t1\t\t\t\t\n" if cmd[1:2] == ("ps",) else ""
        row = self.k.bots()[0]
        self.assertEqual({k: row[k] for k in ("behind", "restartable", "protocol", "session_gen")},
                         {"behind": True, "restartable": False, "protocol": 1, "session_gen": "2"})


class Board(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        r = self.client.post('/api/agents', json={'handle': 'keeper-box', 'kind': 'keeper'})
        self.keeper = {'Authorization': 'Bearer ' + r.json()['token']}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def put(self, report):
        r = self.client.put('/api/fleet/hosts/box', headers=self.keeper, json=report)
        self.assertEqual(r.status_code, 200, r.text)
        return fleet.hosts()[0]

    def test_new_fields_are_kept_and_an_older_report_still_goes_in(self):
        h = self.put({'keeper': {'loaded': 'abc', 'started': '2026-10-22T00:00:00+00:00', 'protocol': 1},
                      'bots': [{'handle': 'ann', 'status': 'waiting', 'launcher': 'v1.0.1', 'behind': True,
                                'restartable': False, 'protocol': 1, 'session_gen': '2'}]})
        self.assertEqual(h['keeper'], {'loaded': 'abc', 'started': '2026-10-22T00:00:00+00:00', 'protocol': 1})
        b = h['bots'][0]
        self.assertEqual((b['behind'], b['restartable'], b['protocol'], b['session_gen']), (True, False, 1, '2'))
        h = self.put({'bots': [{'handle': 'ann', 'status': 'waiting'}]})     # a keeper from before #238
        self.assertIsNone(h['keeper'])
        b = h['bots'][0]
        self.assertEqual((b['behind'], b['restartable'], b['protocol'], b['session_gen']), (None, None, None, ''))


if __name__ == "__main__":
    unittest.main()
