"""The runner end to end: it runs what the board says is enabled, answers tests, reports failures.

The real board under uvicorn on a free port, and bin/routines as a subprocess, as
a box would run it. Needs the board's requirements (uvicorn).
Run with python -m unittest discover -s tests.
"""
import json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTINES = str(ROOT / "bin" / "routines")
CHECK = """#!/usr/bin/env python3
import json, os
print(json.dumps({"work": True, "found": ["mark=" + (os.environ.get("ROUTINE_WATERMARK") or "none")], "watermark": {"n": 9}}))
"""
# passes when `routines add` dry-runs it, fails once $HOME/fail exists
BROKEN = """#!/bin/sh
if [ -e "$HOME/fail" ]; then echo oops; exit 3; fi
echo '{"work": false}'
"""


class RoutineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        cls.board = f"http://127.0.0.1:{port}"
        cls.srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=ROOT,
                                   env=dict(os.environ, BOT_BOARD_DB=os.path.join(cls.tmp.name, "b.db"),
                                            BOT_BOARD_PROJECT_ADMIN_KEY="k"),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.board + "/healthz", timeout=1); break
            except OSError:
                time.sleep(0.1)
        cls.home = os.path.join(cls.tmp.name, "home")
        cls.cfg = os.path.join(cls.home, ".config", "bot_board")
        os.makedirs(cls.cfg)
        cls.human = cls.api("POST", "/api/agents", {"handle": "jac", "kind": "human"})["token"]
        with open(os.path.join(cls.cfg, "jac.token"), "w") as fh:
            fh.write(cls.human)
        cls.api("POST", "/api/projects/access", {"key": "k"}, cls.human)
        cls.api("POST", "/api/projects", {"slug": "admin", "name": "Admin", "purpose": "Running the fleet"}, cls.human)

    @classmethod
    def tearDownClass(cls):
        cls.srv.terminate(); cls.srv.wait(10)
        cls.tmp.cleanup()

    @classmethod
    def api(cls, method, path, body=None, token=None):
        req = urllib.request.Request(cls.board + path, method=method, data=None if body is None else json.dumps(body).encode())
        req.add_header("content-type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(req) as r:
            return json.load(r)

    def routines(self, *args):
        p = subprocess.run([sys.executable, ROUTINES, *args], capture_output=True, text=True,
                           env=dict(os.environ, HOME=self.home, BOARD=self.board, BOT_CONFIG=os.devnull))
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        return p.stdout

    def add(self, name, check=CHECK):
        files = {}
        for k, text in (("check", check), ("prompt", "Look at what was found.")):
            files[k] = os.path.join(self.tmp.name, f"{name}.{k}")
            with open(files[k], "w") as fh:
                fh.write(text)
        self.routines("add", "--as", "jac", "--name", name, "--channel", "proj", "--check", files["check"],
                      "--prompt", files["prompt"])
        return next(r["id"] for r in self.api("GET", "/api/routines")["routines"] if r["name"] == name)

    def routine(self, rid):
        return self.api("GET", f"/api/routines/{rid}")

    def ask(self, rid, action):
        return self.api("POST", f"/api/routines/{rid}/{action}", {}, self.human)

    def state(self):
        try:
            with open(os.path.join(self.cfg, "routines", "state.json")) as fh:
                return json.load(fh)
        except OSError:
            return {}

    def project_posts(self, rid):
        return [m for m in self.api("GET", "/api/messages?channel=proj", token=self.human)["messages"]
                if m["meta"].get("routine") == rid]

    def test_paused_routine_is_tested_once_without_firing(self):
        rid = self.add("paused-one")
        self.ask(rid, "test")
        self.routines("once")
        t = self.routine(rid)["last_test"]
        self.assertFalse(self.routine(rid)["test_pending"])
        self.assertEqual((t["ok"], t["work"], t["found"], t["watermark"], t["error"]),
                         (True, True, ["mark=none"], {"n": 9}, None))
        self.assertEqual(self.project_posts(rid), [], "a test must not fire a job")
        st = self.state()[str(rid)]
        self.assertNotIn("watermark", st); self.assertNotIn("job", st)
        self.routines("once")                            # answered once per ask
        self.assertEqual(self.routine(rid)["last_test"]["at"], t["at"])

    def test_enabled_routine_fires_and_a_test_keeps_the_watermark(self):
        rid = self.add("enabled-one")
        self.ask(rid, "enable")
        st = self.state(); st[str(rid)] = {"watermark": {"n": 5}, "version": 1, "checked_at": time.time()}
        os.makedirs(os.path.join(self.cfg, "routines"), exist_ok=True)
        with open(os.path.join(self.cfg, "routines", "state.json"), "w") as fh:
            json.dump(st, fh)
        self.ask(rid, "test")
        self.routines("once")
        self.assertEqual(self.routine(rid)["last_test"]["found"], ['mark={"n": 5}'])
        self.assertEqual(self.state()[str(rid)]["watermark"], {"n": 5}, "a test must leave the watermark alone")
        st = self.state(); st[str(rid)]["checked_at"] = 0
        with open(os.path.join(self.cfg, "routines", "state.json"), "w") as fh:
            json.dump(st, fh)
        self.routines("once")
        (job,) = self.project_posts(rid)
        self.assertIn('mark={"n": 5}', job["body"])
        self.assertEqual(self.routine(rid)["jobs"][0]["id"], job["id"])

    def test_a_failing_check_stops_the_routine_and_says_so_in_heads_up(self):
        rid = self.add("broken", check=BROKEN)
        self.ask(rid, "enable")
        open(os.path.join(self.home, "fail"), "w").close()
        try:
            self.routines("once")
        finally:
            os.remove(os.path.join(self.home, "fail"))
        r = self.routine(rid)
        self.assertEqual(r["state"], "failed")
        self.assertIn("exited 3", r["failure"])
        told = [m for m in self.api("GET", "/api/messages?channel=heads-up", token=self.human)["messages"]
                if m["meta"].get("routine") == rid]
        self.assertEqual(len(told), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
