"""#228: a restart the bot's launcher never carries out (one from before bot restart existed)
is done by the keeper as a stop and a start. Run with python -m unittest discover -s tests."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_fleet_kill import load_keeper     # noqa: E402


class ConfirmRestart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        saved = os.environ.get('HOME'), os.environ.get('BOT_CONFIG')
        try:
            self.k = load_keeper(self.tmp.name)
        finally:
            for k, v in zip(('HOME', 'BOT_CONFIG'), saved):
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
        self.hands = self.k.Hands()
        self.spawned = []
        self.hands.spawn = lambda h, cmd: self.spawned.append(cmd[1:])
        self.state = Path(self.k.CFG) / "bots" / "jonas"
        self.state.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def marker(self, age):
        m = self.state / "restart"
        m.touch()
        os.utime(m, (time.time() - age, time.time() - age))

    def seen(self, status):
        return [{"handle": "jonas", "harness": "claude", "status": status}]

    def test_a_fresh_marker_is_left_to_the_launcher(self):
        self.marker(30)
        self.assertEqual(self.hands.reconcile([], self.seen("waiting")), {})
        self.assertEqual(self.spawned, [])

    def test_an_ignored_restart_becomes_a_stop_and_a_start(self):
        self.marker(self.k.RESTART_GRACE + 60)
        problems = self.hands.reconcile([], self.seen("waiting"))
        self.assertEqual(self.spawned, [["stop", "jonas"]])
        self.assertIn("did not restart on its own", problems["jonas"])
        self.hands.reconcile([], self.seen("waiting, stopping"))     # still going down: nothing new
        self.assertEqual(self.spawned, [["stop", "jonas"]])
        self.hands.reconcile([], self.seen("stopped"))
        self.assertEqual(self.spawned, [["stop", "jonas"], ["claude", "--name", "jonas"]])

    def test_a_bot_the_board_wants_running_is_started_by_its_request(self):
        self.marker(self.k.RESTART_GRACE + 60)
        self.hands.reconcile([], self.seen("waiting"))
        row = {"handle": "jonas", "harness": "claude", "run": True, "set_by": "jac", "set_at": "2026-10-22T00:00:00Z",
               "move_to": None, "fetch": None, "kill": None}
        self.hands.reconcile([row], self.seen("stopped"))
        self.assertEqual([c for c in self.spawned if c[0] == "claude"], [["claude", "--name", "jonas"]],
                         "started once, by the request")

    def test_a_busy_bot_is_never_interrupted(self):
        self.marker(self.k.RESTART_GRACE + 600)
        self.hands.reconcile([], self.seen("running 7701"))
        self.assertEqual(self.spawned, [])


if __name__ == "__main__":
    unittest.main()
