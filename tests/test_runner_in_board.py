"""BOT_BOARD_ROUTINES=1: the board runs the routines runner itself.

End to end: the board under uvicorn with the runner on, a routine posted and
enabled over the API, and the job it fires, with no bin/routines process.
Run with python -m unittest discover -s tests.
"""
import json, os, socket, stat, subprocess, sys, tempfile, time, unittest, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = """#!/bin/sh
echo '{"work": true, "found": ["something to do"], "watermark": 1}'
"""


class RunnerInBoard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        cls.board = f"http://127.0.0.1:{port}"
        cls.cfg = os.path.join(cls.tmp.name, "routines")
        cls.srv = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=ROOT,
            env=dict(os.environ, BOT_BOARD_DB=os.path.join(cls.tmp.name, "b.db"), BOT_BOARD_ROUTINES="1",
                     ROUTINES_CONFIG_DIR=cls.cfg, ROUTINES_INTERVAL="1", BOARD=cls.board, BOT_BOARD_PROJECT_ADMIN_KEY="k"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.board + "/healthz", timeout=1); break
            except OSError:
                time.sleep(0.1)
        cls.human = cls.api("POST", "/api/agents", {"handle": "jac", "kind": "human"})["token"]
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

    def test_the_board_fires_an_enabled_routine_itself(self):
        token_file = os.path.join(self.cfg, "routines.token")
        self.assertTrue(os.path.getsize(token_file))
        self.assertEqual(stat.S_IMODE(os.stat(token_file).st_mode), 0o600)
        human = self.human
        got = self.api("POST", "/api/routines", {"name": "tick", "channel": "proj", "check": CHECK,
                                                 "prompt": "Do it.", "every": 1}, human)
        self.api("POST", f"/api/routines/{got['id']}/enable", {}, human)    # a manager of admin needs no cited yes
        fired = []
        for _ in range(100):
            fired = [m for m in self.api("GET", "/api/messages?channel=proj", token=human)["messages"]
                     if m["meta"].get("routine") == got["id"]]
            if fired:
                break
            time.sleep(0.1)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["author"], "routines")
        self.assertIn("something to do", fired[0]["body"])

    def test_checks_get_the_fleet_secrets_set_on_the_board(self):
        human = self.human
        self.api("PUT", "/api/fleet/secrets/ROUTINE_PROBE_TOKEN", {"value": "s3cret-from-board"}, human)
        check = '#!/bin/sh\necho "{\\"work\\": true, \\"found\\": [\\"saw $ROUTINE_PROBE_TOKEN\\"], \\"watermark\\": 1}"\n'
        got = self.api("POST", "/api/routines", {"name": "probe", "channel": "secrets", "check": check,
                                                 "prompt": "Do it.", "every": 1}, human)
        self.api("POST", f"/api/routines/{got['id']}/enable", {}, human)
        fired = []
        for _ in range(100):
            fired = [m for m in self.api("GET", "/api/messages?channel=secrets", token=human)["messages"]
                     if m["meta"].get("routine") == got["id"]]
            if fired:
                break
            time.sleep(0.1)
        self.assertEqual(len(fired), 1)
        self.assertIn("saw s3cret-from-board", fired[0]["body"])


if __name__ == "__main__":
    unittest.main()
