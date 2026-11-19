"""A restart button under a bot on /fleet (#15251): the board records the request, the keeper passes it to
bin/bot restart once, the launcher says what happens, and the row shows each step. Run with
python -m unittest discover -s tests."""
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from fastapi.testclient import TestClient       # noqa: E402
from app import db, main, web                    # noqa: E402

class LauncherSide(unittest.TestCase):
    """bin/bot restart against a stand-in for a running launcher (a process named bot with a pidfile, and a ps that
    reports it), so the answer it records is checked without starting a harness."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.state = t / ".config" / "bot_board" / "bots" / "tbot"
        self.state.mkdir(parents=True)
        (t / "stub").mkdir(); (t / "shim").mkdir()
        (t / "stub" / "bot").write_text("#!/bin/bash\nsleep 60\n")
        (t / "shim" / "ps").write_text(f'#!/bin/bash\necho "bash {t}/stub/bot"\n')
        for f in (t / "stub" / "bot", t / "shim" / "ps"):
            f.chmod(0o755)
        self.proc = subprocess.Popen(["bash", str(t / "stub" / "bot")])
        (self.state / "pid").write_text(str(self.proc.pid))
        self.env = {**os.environ, "HOME": str(t), "PATH": f"{t}/shim:{os.environ['PATH']}"}
        self.env.pop("BOT_CONFIG", None)

    def tearDown(self):
        self.proc.kill(); self.proc.wait()
        self.tmp.cleanup()

    def restart(self, *args):
        return subprocess.run([str(ROOT / "bin" / "bot"), "restart", "tbot", *args], env=self.env, capture_output=True,
                              text=True, timeout=10)

    def read(self, name):
        return (self.state / name).read_text().strip()

    def test_a_request_is_taken_up_once_and_the_launcher_says_what_follows(self):
        r = self.restart("--asked", "A1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.read("restart.done"), self.read("restart.said")), ("A1", "tbot is restarting"))
        self.assertTrue((self.state / "restart").exists(), "the launcher is asked to restart")
        (self.state / "restart.said").write_text("kept\n")
        self.restart("--asked", "A1")     # the keeper passing the same request again
        self.assertEqual(self.read("restart.said"), "kept", "a request is carried out once")
        (self.state / "busy").write_text("#12\n")
        self.restart("--asked", "A2")
        self.assertEqual(self.read("restart.said"), "tbot will restart when its run in #12 returns")

    def test_a_bot_that_is_not_running_says_so_and_by_hand_records_nothing(self):
        self.restart()
        self.assertFalse((self.state / "restart.done").exists(), "by hand, there is no request to record")
        self.assertTrue((self.state / "restart").exists())
        self.proc.kill(); self.proc.wait()
        r = self.restart("--asked", "A3")
        self.assertIn("not running", r.stdout)
        self.assertIn("start it from /fleet", self.read("restart.said"))
        self.assertEqual(self.read("restart.done"), "A3")

class Board(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register("op", "human")
        self.keeper = self.register("keeper-box", "keeper")
        self.bot = self.register("tarzan", "claude-code")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post("/api/agents", json={"handle": handle, "kind": kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": "Bearer " + r.json()["token"]}

    def report(self, status="waiting", **extra):
        r = self.client.put("/api/fleet/hosts/box", headers=self.keeper, json={"bots": [
            {"handle": "tarzan", "harness": "claude", "status": status, "pool": True, **extra}]})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["want"]

    def note(self):
        page = self.client.get("/fleet").text
        i = page.find("restart asked"); j = page.find("its box got the restart"); k = page.find("restarted ")
        return page, [x for x in (i, j, k) if x >= 0]

    def test_the_button_shows_only_for_a_running_bot_and_only_a_human_can_press_it(self):
        self.report()
        self.assertIn('class="restart" data-handle="tarzan"', self.client.get("/fleet").text)
        self.report(status="stopped")
        self.assertNotIn('class="restart" data-handle="tarzan"', self.client.get("/fleet").text)
        self.assertEqual(self.client.post("/api/fleet/bots/tarzan/restart", headers=self.keeper).status_code, 403)
        self.assertEqual(self.client.post("/api/fleet/bots/tarzan/restart", headers=self.bot).status_code, 403)
        r = self.client.post("/api/fleet/bots/tarzan/restart", headers=self.human)
        self.assertEqual(r.status_code, 409, "a stopped bot is started, not restarted")
        self.assertIn("start it instead", r.text)
        self.assertEqual(self.client.post("/api/fleet/bots/nobody/restart", headers=self.human).status_code, 409)

    def test_the_row_walks_from_asked_to_the_box_answer_to_done(self):
        self.report()
        r = self.client.post("/api/fleet/bots/tarzan/restart", headers=self.human)
        self.assertEqual(r.status_code, 200, r.text)
        want = self.report()
        self.assertTrue(want[0]["restart"])
        self.assertTrue(want[0]["run"], "a bot nobody had asked anything of stays as it is")
        asked = want[0]["restart"]
        page = self.client.get("/fleet").text
        self.assertIn("restart asked", page)
        self.assertIn("waiting for its box to pick it up", page)
        self.assertIn("Next:", page)
        self.assertIn('data-live="1"', page)
        self.report(restart_done=asked, restart_said="tarzan will restart when its run in #12 returns", restart_pending=True)
        page = self.client.get("/fleet").text
        self.assertIn("its box got the restart: tarzan will restart when its run in #12 returns", page)
        self.assertIn('data-live="1"', page)
        self.report(restart_done=asked, restart_said="tarzan is restarting", restart_pending=False)
        page = self.client.get("/fleet").text
        self.assertIn("restarted ", page)
        self.assertIn("the same bot, on the code now on its box", page)
        self.assertNotIn('data-live="1"', page, "settled: the page stops asking")

    def test_a_box_that_never_answers_is_said_so_with_what_to_do(self):
        self.report()
        self.client.post("/api/fleet/bots/tarzan/restart", headers=self.human)
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
        c = db.conn(); c.execute("UPDATE fleet_bots SET restart=? WHERE handle='tarzan'", (old,)); c.commit()
        self.report()
        page = self.client.get("/fleet").text
        self.assertIn("its box has not answered", page)
        self.assertIn("stop the bot and start it again", page)
        self.assertNotIn('data-live="1"', page)

    def test_the_keeper_passes_each_request_to_bin_bot_once(self):
        from test_fleet_kill import load_keeper
        home = os.environ.get("HOME"), os.environ.get("BOT_CONFIG")
        try:
            keeper = load_keeper(self.tmp.name)
        finally:
            for k, v in zip(("HOME", "BOT_CONFIG"), home):
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
        hands = keeper.Hands()
        spawned = []
        hands.spawn = lambda h, cmd: spawned.append(cmd[1:])
        seen = [{"handle": "tarzan", "status": "waiting"}]
        want = [{"handle": "tarzan", "harness": "claude", "run": True, "restart": "2026-11-19T10:00:00+00:00", "set_by": "op",
                 "set_at": "2026-11-19T09:00:00+00:00", "move_to": None, "fetch": None}]
        hands.reconcile(want, seen)
        hands.reconcile(want, seen)
        self.assertEqual(spawned, [["restart", "tarzan", "--asked", "2026-11-19T10:00:00+00:00"]])
        hands.reconcile([{**want[0], "restart": "2026-11-19T11:00:00+00:00"}], seen)
        self.assertEqual(len(spawned), 2)

if __name__ == "__main__":
    unittest.main()
