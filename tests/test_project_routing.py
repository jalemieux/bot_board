"""bin/bot routes a pool bot's work by the board's project records.

Runs the real launcher against a small fake board and a fake `claude` that
records where each run happened and what it was told. Needs bash, git and
python3; no dependencies. `python3 tests/test_project_routing.py` or pytest.
"""
import json, os, shutil, signal, subprocess, sys, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT = os.path.join(ROOT, "bin", "bot")
ME = "tst"


def msg(mid, board, body, meta=None, mention=False):
    return {"id": mid, "board": board, "thread_id": None, "author": "jac", "body": body,
            "meta": meta or {}, "mention": mention}


def scope(slug, readiness="ready", body="", archived=False, **kw):
    return {"slug": slug, "channel": slug, "archived": archived, "readiness": readiness,
            "context": {"revision": 3 if body else None, "body": body, "sources": ["thread 12"] if body else []}, **kw}


class FakeBoard:
    """Messages, an inbox, and GET /api/channels/{slug}/project from a table."""

    def __init__(self, messages, routes, has_lookup=True, flaky=()):
        self.messages, self.routes, self.has_lookup = messages, routes, has_lookup
        self.flaky = dict.fromkeys(flaky, 1)   # channel -> lookups left that fail with 500
        self.lookups = []
        board = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def send(self, code, data):
                b = json.dumps(data, separators=(",", ":")).encode()   # compact, as the board sends it
                self.send_response(code); self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length") or 0))
                if self.path == "/api/agents": return self.send(200, {"token": "t0k"})
                return self.send(200, {"message": {"id": 999}})

            def do_GET(self):
                u = urlparse(self.path); q = parse_qs(u.query); path = u.path
                since = int(q.get("since", ["0"])[0])
                if path == "/api/me": return self.send(200, {"agent": {"handle": ME}, "cursor": 0})
                if path in ("/api/agents", "/api/channels"): return self.send(200, {"agents": [], "channels": []})
                if path in ("/api/messages", "/api/inbox"):
                    new = [m for m in board.messages if m["id"] > since and (path == "/api/messages" or m["mention"])]
                    if not new and q.get("wait"): time.sleep(1)
                    return self.send(200, {"messages": new, "cursor": max([m["id"] for m in new], default=since)})
                if path.startswith("/api/channels/") and path.endswith("/project"):
                    ch = unquote(path[len("/api/channels/"):-len("/project")])
                    board.lookups.append(ch)
                    if not board.has_lookup: return self.send(404, {"detail": "Not Found"})
                    if board.flaky.get(ch):
                        board.flaky[ch] -= 1
                        return self.send(500, {"detail": "boom"})
                    return self.send(200, board.routes.get(ch, {"project": None, "group": None}))
                return self.send(404, {"detail": "Not Found"})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_port
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


FAKE_CLAUDE = """#!/usr/bin/env python3
import sys, os, json
a = sys.argv[1:]
sid = a[a.index("--session-id") + 1] if "--session-id" in a else a[a.index("--resume") + 1]
with open(os.environ["ROUTING_TEST_RUNS"], "a") as f:
    f.write(json.dumps({"cwd": os.getcwd(), "sid": sid, "prompt": a[-1]}) + "\\n")
"""


class Routing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="routing-")
        self.home, self.pool = os.path.join(self.tmp, "home"), os.path.join(self.tmp, "pool")
        bindir = os.path.join(self.tmp, "bin")
        for d in (self.home, self.pool, bindir): os.makedirs(d)
        with open(os.path.join(bindir, "claude"), "w") as f: f.write(FAKE_CLAUDE)
        os.chmod(os.path.join(bindir, "claude"), 0o755)
        self.runs = os.path.join(self.tmp, "runs.jsonl")
        self.env = dict(os.environ, HOME=self.home, PATH=bindir + os.pathsep + os.environ["PATH"],
                        ROUTING_TEST_RUNS=self.runs, BOT_WAIT="1", BOT_JITTER="0", BOT_CONFIG=os.path.join(self.tmp, "none"),
                        GIT_TERMINAL_PROMPT="0")
        self.env.pop("BOT_SKILLS", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def remote(self, name):
        """A git repo to clone from, standing in for a GitHub URL."""
        d = os.path.join(self.tmp, "remotes", name)
        os.makedirs(d)
        git = lambda *a: subprocess.run(["git", "-C", d, *a], check=True, capture_output=True)
        git("init", "-q")
        with open(os.path.join(d, "README"), "w") as f: f.write(name)
        git("add", "."); git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        return "file://" + d

    def run_bot(self, board, until, timeout=40):
        """Start the launcher, wait until until(runs) holds, stop it, return the runs."""
        p = subprocess.Popen([BOT, "claude", "--pool", self.pool, "--name", ME], env=dict(self.env, BOARD=board.url),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        runs, end = [], time.time() + timeout
        try:
            while time.time() < end:
                time.sleep(0.5)
                if os.path.exists(self.runs):
                    with open(self.runs) as f: runs = [json.loads(l) for l in f]
                if until(runs): break
        finally:
            os.killpg(p.pid, signal.SIGTERM); p.wait(10)
        with open(os.path.join(self.home, ".config/bot_board/bots", ME, "log")) as f: self.log = f.read()
        return [r for r in runs if "read the rules and return" not in r["prompt"]]   # skip the first turn

    def test_groups_share_their_project_session_and_context(self):
        art_url, assets_url = self.remote("game"), self.remote("assets")
        game = scope("game", body="Ships are wedge-shaped.", name="Game",
                     repositories=[{"name": "game", "url": art_url}, {"name": "assets", "url": assets_url}])
        routes = {
            "game": {"project": game, "group": None},
            "game-art": {"project": game, "group": scope("art", body="Palette: teal and rust.")},
            "game-lore": {"project": game, "group": scope("lore", readiness="unseeded")},
            "old-arc": {"project": scope("old", archived=True, repositories=[]), "group": None},
            "flaky": {"project": game, "group": None},
        }
        board = FakeBoard([
            msg(1, "game-art", "draw a mining ship"),
            msg(2, "game", "@tst status?", mention=True),
            msg(3, "game-art", "context revised", meta={"kind": "context_update"}),
            msg(4, "game-lore", "write the backstory"),
            msg(5, "game-lore", "seed lore from threads 40-60", meta={"kind": "context_seed"}),
            msg(6, "game-lore", "@tst can you write lore?", mention=True),
            msg(7, "old-arc", "anything here?"),
            msg(8, "flaky", "after a hiccup"),
            msg(9, "help", "hello fleet"),
        ], routes, flaky=["flaky"])
        try:
            runs = self.run_bot(board, lambda rs: any("after a hiccup" in r["prompt"] for r in rs))
        finally:
            board.close()
        game_dir = os.path.join(self.pool, "game")
        in_game = [r for r in runs if r["cwd"] == game_dir]
        self.assertTrue(in_game, "no run in the project's checkout: %s" % runs)
        first = in_game[0]["prompt"]
        # every group of the project lands in the one project session, with both contexts
        for text in ("draw a mining ship", "@tst status?", "seed lore from threads 40-60",
                     "Ships are wedge-shaped.", "Palette: teal and rust.", "kind context_seed"):
            self.assertIn(text, first)
        self.assertEqual(len({r["sid"] for r in in_game}), 1, "groups did not share the project session")
        # each message sits under its own header, with its kind and any note on that line
        self.assertIn("from @jac, reply_to 1]\ndraw a mining ship", first)
        self.assertIn("reply_to 5, kind context_seed]\nseed lore from threads 40-60", first)
        self.assertRegex(first, r"reply_to 6\] Note: group lore is unseeded[^\n]*\n@tst can you write lore\?")
        # not run: the context event, normal work in an unseeded group, an archived project
        prompts = "\n".join(r["prompt"] for r in runs)
        for text in ("context revised", "write the backstory", "anything here?"):
            self.assertNotIn(text, prompts)
        # addressed work in an unseeded group is passed on, but marked as not ready
        self.assertIn("can you write lore?", first)
        self.assertIn("group lore is unseeded", first)
        # the secondary repository sits beside the primary; no checkout is named after a group or channel
        self.assertTrue(os.path.isdir(os.path.join(self.pool, "game--assets", ".git")))
        self.assertEqual(sorted(os.listdir(self.pool)), ["game", "game--assets"])
        # the failed lookup held the message and the next batch delivered it, in the project session
        self.assertIn("held: project lookup failed", self.log)
        late = [r for r in in_game if "after a hiccup" in r["prompt"]]
        self.assertTrue(late and late[0] is not in_game[0])
        # the fleet channel still runs at the pool root, outside any project
        root = [r for r in runs if r["cwd"] == self.pool]
        self.assertTrue(any("hello fleet" in r["prompt"] for r in root))
        self.assertNotIn("Ships are wedge-shaped.", "\n".join(r["prompt"] for r in root))
        # one lookup per channel per batch, never one for a fleet channel
        self.assertNotIn("help", board.lookups)
        self.assertEqual(board.lookups.count("game-art"), 1)

    def test_old_board_keeps_channel_routing(self):
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        board = FakeBoard([msg(1, "wordsnap", "fix the popup"), msg(2, "help", "anyone?")], {}, has_lookup=False)
        try:
            runs = self.run_bot(board, lambda rs: sum("fix the popup" in r["prompt"] or "anyone?" in r["prompt"]
                                                    for r in rs) >= 2)
        finally:
            board.close()
        where = {r["cwd"] for r in runs if "fix the popup" in r["prompt"]}
        self.assertEqual(where, {os.path.join(self.pool, "wordsnap")})
        self.assertTrue(any(r["cwd"] == self.pool and "anyone?" in r["prompt"] for r in runs))
        self.assertTrue(os.path.exists(os.path.join(self.home, ".config/bot_board/bots", ME, "session.wordsnap")))
        self.assertNotIn("held", self.log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
