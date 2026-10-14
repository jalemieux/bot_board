"""A human's `test` in a routine's thread: the runner runs the check once and says what it found.

End to end: the real board under uvicorn on a free port, and bin/routines as a
subprocess, as a box would run it. Needs the board's requirements (uvicorn).
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
                                   env=dict(os.environ, BOT_BOARD_DB=os.path.join(cls.tmp.name, "b.db")),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.board + "/healthz", timeout=1); break
            except OSError:
                time.sleep(0.1)
        cls.home = os.path.join(cls.tmp.name, "home")
        cls.cfg = os.path.join(cls.home, ".config", "bot_board")
        os.makedirs(cls.cfg)
        cls.human = cls.register("jac", "human")
        cls.bot = cls.register("ada", "claude-code")
        with open(os.path.join(cls.cfg, "ada.token"), "w") as fh:
            fh.write(cls.bot)

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

    @classmethod
    def register(cls, handle, kind):
        return cls.api("POST", "/api/agents", {"handle": handle, "kind": kind})["token"]

    def routines(self, *args):
        p = subprocess.run([sys.executable, ROUTINES, *args], capture_output=True, text=True,
                           env=dict(os.environ, HOME=self.home, BOARD=self.board, ROUTINES_OPERATORS=""))
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        return p.stdout

    def add(self, name, check=CHECK):
        files = {}
        for k, text in (("check", check), ("prompt", "Look at what was found.")):
            files[k] = os.path.join(self.tmp.name, f"{name}.{k}")
            with open(files[k], "w") as fh:
                fh.write(text)
        self.routines("add", "--as", "ada", "--name", name, "--channel", "proj", "--check", files["check"],
                      "--prompt", files["prompt"])
        return max(m["id"] for m in self.api("GET", "/api/messages?channel=admin", token=self.human)["messages"]
                   if m["meta"].get("routine", {}).get("name") == name)

    def say(self, thread, body, token=None):
        return self.api("POST", "/api/messages", {"channel": "admin", "body": body, "reply_to": thread},
                        token or self.human)["message"]["id"]

    def answers(self, thread):
        return [m["meta"]["routine_test"] for m in self.api("GET", f"/api/threads/{thread}", token=self.human)["messages"]
                if isinstance(m["meta"].get("routine_test"), dict)]

    def state(self):
        try:
            return json.load(open(os.path.join(self.cfg, "routines", "state.json")))
        except OSError:
            return {}

    def project_posts(self):
        return self.api("GET", "/api/messages?channel=proj", token=self.human)["messages"]

    def test_paused_routine_is_tested_once_without_firing(self):
        t = self.add("paused-one")
        asked = self.say(t, "test")
        self.say(t, "test", token=self.bot)              # a bot's `test` does not count
        self.routines("once")
        got = self.answers(t)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["for"], asked)
        self.assertTrue(got[0]["ok"] and got[0]["work"])
        self.assertEqual(got[0]["found"], ["mark=none"])
        self.assertEqual(got[0]["watermark"], {"n": 9})
        self.assertIsNone(got[0]["error"])
        self.assertEqual(self.project_posts(), [], "a test must not fire a job")
        st = self.state()[str(t)]
        self.assertEqual(st.get("tested"), asked)
        self.assertNotIn("watermark", st); self.assertNotIn("job", st)
        self.routines("once")                            # handled once per test message
        self.assertEqual(len(self.answers(t)), 1)

    def test_enabled_routine_uses_the_current_watermark_and_keeps_it(self):
        t = self.add("enabled-one")
        code = self.api("GET", "/api/messages?channel=admin", token=self.human)["messages"]
        code = next(m for m in code if m["id"] == t)["body"].split("enable ")[1].split("`")[0]
        self.say(t, f"enable {code}")
        st = self.state(); st[str(t)] = {"watermark": {"n": 5}, "version": t, "checked_at": time.time()}
        os.makedirs(os.path.join(self.cfg, "routines"), exist_ok=True)
        json.dump(st, open(os.path.join(self.cfg, "routines", "state.json"), "w"))
        asked = self.say(t, "Test.")
        self.routines("once")
        got = self.answers(t)
        self.assertEqual([a["for"] for a in got], [asked], got)
        self.assertTrue(got[0]["ok"], got[0])
        self.assertEqual(got[0]["found"], ['mark={"n": 5}'])
        st = self.state()[str(t)]
        self.assertEqual(st["watermark"], {"n": 5}, "a test must leave the watermark alone")
        self.assertNotIn("job", st)

    def test_failing_check_and_invalid_definition_are_answered_with_the_reason(self):
        t = self.add("broken", check=BROKEN)
        open(os.path.join(self.home, "fail"), "w").close()
        self.say(t, "test")
        bad = self.api("POST", "/api/messages", {"channel": "admin", "body": "not what routines add writes",
                       "meta": {"routine": {"name": "hand-made"}}}, self.human)["message"]["id"]
        self.say(bad, "test")
        self.routines("once")
        (b,) = self.answers(t)
        self.assertFalse(b["ok"]); self.assertIn("exited 3", b["error"])
        (i,) = self.answers(bad)
        self.assertFalse(i["ok"]); self.assertIn("cannot run", i["error"])
        self.assertFalse(any("Routine `broken` stopped" in m["body"]
                             for m in self.api("GET", f"/api/threads/{t}", token=self.human)["messages"]),
                         "a failed test must not fail the routine")


if __name__ == "__main__":
    unittest.main(verbosity=2)
