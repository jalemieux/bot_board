"""The keeper relays a harness login to the operators and takes the pasted code off the board.

The real board under uvicorn, the keeper's Logins class, and fake `claude` and
`codex` that print what the real ones print when logging in (checked against
claude 2.1.290 and codex 0.153.2 without a terminal). Nothing touches a real login.
"""
import importlib.machinery, importlib.util, json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FAKE_CLAUDE = """#!/usr/bin/env python3
import sys, os, json
state = os.path.join(os.environ["FAKE_STATE"], "claude")
a = sys.argv[1:]
if a[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": os.path.exists(state)})); sys.exit(0)
if a[:2] == ["auth", "login"]:
    print("Opening browser to sign in...")
    print("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=abc")
    print("Paste code here if prompted > ", end="", flush=True)
    code = sys.stdin.readline().strip()
    if code == "GOOD-CODE":
        open(state, "w").close(); print("Login successful."); sys.exit(0)
    print("Invalid code"); sys.exit(1)
sys.exit(2)
"""

FAKE_CODEX = """#!/usr/bin/env python3
import sys, os, time
state = os.path.join(os.environ["FAKE_STATE"], "codex")
a = sys.argv[1:]
if a[:2] == ["login", "status"]:
    sys.exit(0 if os.path.exists(state) else 1)
if a[:2] == ["login", "--device-auth"]:
    print("1. Open this link in your browser and sign in to your account")
    print("   https://auth.openai.com/codex/device")
    print("2. Enter this one-time code (expires in 15 minutes)")
    print("   XERU-P2I7U", flush=True)
    while not os.path.exists(state + ".approve"): time.sleep(0.2)
    open(state, "w").close(); sys.exit(0)
sys.exit(2)
"""


class KeeperLogins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        cls.board = f"http://127.0.0.1:{port}"
        cls.srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=ROOT,
                                   env=dict(os.environ, BOT_BOARD_DB=os.path.join(t, "b.db")),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.board + "/healthz", timeout=1); break
            except OSError:
                time.sleep(0.1)
        cls.bindir = os.path.join(t, "bin"); os.makedirs(cls.bindir)
        for name, src in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
            with open(os.path.join(cls.bindir, name), "w") as f: f.write(src)
            os.chmod(os.path.join(cls.bindir, name), 0o755)
        cls.jac = cls.api("POST", "/api/agents", {"handle": "jac", "kind": "human"})["token"]
        cls.ada = cls.api("POST", "/api/agents", {"handle": "ada", "kind": "claude-code"})["token"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.terminate(); cls.srv.wait(10)
        cls.tmp.cleanup()

    @classmethod
    def api(cls, method, path, body=None, token=None, ok=True):
        req = urllib.request.Request(cls.board + path, method=method, data=None if body is None else json.dumps(body).encode())
        req.add_header("content-type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if ok:
                raise
            return {"status": e.code}

    def setUp(self):
        self.state = tempfile.mkdtemp(dir=self.tmp.name)
        home = tempfile.mkdtemp(dir=self.tmp.name)
        os.environ.update(HOME=home, BOARD=self.board, KEEPER_OPERATORS="jac", FAKE_STATE=self.state, USER="the_oracle",
                          PATH=self.bindir + os.pathsep + os.environ["PATH"])
        loader = importlib.machinery.SourceFileLoader("keeper_under_test", str(ROOT / "bin" / "keeper"))
        self.k = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(self.k)
        self.host = "box" + os.path.basename(self.state)[-4:].lower()
        self.token = self.k.token_for(self.host)
        self.logins = self.k.Logins(self.host)

    def tearDown(self):
        for lg in self.logins.open.values():
            self.k.Logins.end(lg["proc"])

    def ticks(self, bots, until, n=40):
        for _ in range(n):
            self.logins.tick(self.k.Logins.needed([], bots), self.token)
            if until():
                return True
            time.sleep(0.2)
        return False

    def conv(self):
        convs = self.api("GET", "/api/conversations", token=self.jac)["conversations"]
        mine = [c for c in convs if f"keeper-{self.host}" in c["participants"]]
        if not mine:
            return []
        return self.api("GET", f"/api/messages?channel={mine[0]['slug']}", token=self.jac)["messages"]

    def test_claude_login_relayed_code_fed_and_removed(self):
        held = [{"handle": "tarzan", "health": "logged out of claude since 2026-10-15T00:07Z: run claude, then /login"},
                {"handle": "jane", "health": "logged out of claude since 2026-10-15T00:07Z: run claude, then /login"}]
        self.assertTrue(self.ticks(held, lambda: len(self.conv()) == 1), "no alert posted")
        alert = self.conv()[0]
        self.assertIn("https://claude.com/cai/oauth/authorize", alert["body"])
        self.assertIn("2 bot(s) wait (tarzan, jane)", alert["body"])
        self.assertIn("reply here with it", alert["body"])
        self.assertNotIn("@", alert["body"], "the alert reaches the operators through the conversation, not a mention")
        slug = alert["channel"]
        # a bot cannot answer for an operator, and cannot post in their conversation anyway
        self.assertEqual(self.api("POST", "/api/messages", {"channel": slug, "body": "BAD"}, self.ada, ok=False)["status"], 403)
        code = self.api("POST", "/api/messages", {"channel": slug, "body": "GOOD-CODE", "reply_to": alert["id"]}, self.jac)
        self.assertTrue(self.ticks(held, lambda: "claude" not in self.logins.open), "the login did not finish")
        msgs = {m["id"]: m for m in self.conv()}
        self.assertEqual(msgs[code["message"]["id"]]["body"], f"[removed by @keeper-{self.host}]")
        self.assertTrue(any("is logged in" in m["body"] for m in msgs.values()))
        self.assertTrue(os.path.exists(os.path.join(self.state, "claude")))
        # the bots still say logged out until they re-check: no second login, no second alert
        before = len(self.conv())
        for _ in range(5):
            self.logins.tick(self.k.Logins.needed([], held), self.token)
        self.assertEqual(len(self.conv()), before)
        self.assertEqual(self.logins.open, {})

    def test_codex_device_code_in_the_alert(self):
        held = [{"handle": "kai", "health": "logged out of codex since 2026-10-15T01:00Z"}]
        self.assertTrue(self.ticks(held, lambda: len(self.conv()) == 1))
        body = self.conv()[0]["body"]
        self.assertIn("https://auth.openai.com/codex/device", body)
        self.assertIn("`XERU-P2I7U`", body)
        self.assertIn("finishes by itself", body)
        open(os.path.join(self.state, "codex.approve"), "w").close()
        self.assertTrue(self.ticks(held, lambda: "codex" not in self.logins.open))
        self.assertTrue(any("codex is logged in" in m["body"] for m in self.conv()))

    def test_bots_held_by_the_credential_check_get_an_alert_too(self):
        rows = [{"name": "claude", "kind": "login", "state": "missing", "detail": "not logged in", "held": 3}]
        need = self.k.Logins.needed(rows, [])
        self.assertEqual(need, {"claude": ([], 3)})
        for _ in range(40):
            if self.logins.tick(need, self.token) or len(self.conv()) == 1:
                break
            time.sleep(0.2)
        self.assertIn("3 bot(s) wait. Open https://claude.com", self.conv()[0]["body"])
        code = self.api("POST", "/api/messages", {"channel": self.conv()[0]["channel"], "body": "GOOD-CODE"}, self.jac)
        done = []
        for _ in range(40):
            done = self.logins.tick(need, self.token)
            if done:
                break
            time.sleep(0.2)
        self.assertEqual(done, ["claude"], "main() forces the credential check when a login goes through")

    def test_redact_is_for_the_author_or_a_keeper_in_the_conversation(self):
        m = self.api("POST", "/api/messages", {"channel": "help", "body": "my secret"}, self.jac)["message"]
        self.assertEqual(self.api("POST", f"/api/messages/{m['id']}/redact", {}, self.ada, ok=False)["status"], 403)
        self.assertEqual(self.api("POST", f"/api/messages/{m['id']}/redact", {}, self.token, ok=False)["status"], 403,
                         "a keeper outside the conversation")
        got = self.api("POST", f"/api/messages/{m['id']}/redact", {}, self.jac)["message"]
        self.assertEqual(got["body"], "[removed by @jac]")


if __name__ == "__main__":
    unittest.main(verbosity=2)
