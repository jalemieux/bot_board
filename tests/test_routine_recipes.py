"""Recipe routines: GitHub issues routed to a project's groups, announced once, chased.

End to end like test_routines_test.py: the real board under uvicorn, bin/routines
as a subprocess, and a fake `gh` on PATH that answers from files in $HOME.
Run with python -m unittest discover -s tests.
"""
import importlib.machinery, importlib.util, json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTINES = str(ROOT / "bin" / "routines")
FAKE_GH = """#!/bin/sh
# gh issue list ... -> $HOME/issues.json ; gh issue view N ... -> $HOME/view-N.json or open and untaken
if [ "$2" = list ]; then cat "$HOME/issues.json"; exit 0; fi
if [ -f "$HOME/view-$3.json" ]; then cat "$HOME/view-$3.json"; exit 0; fi
echo '{"state": "OPEN", "assignees": [], "closedByPullRequestsReferences": []}'
"""


def load_cli():
    loader = importlib.machinery.SourceFileLoader("routines_cli", ROUTINES)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def issue(n, labels=(), assignees=()):
    return {"number": n, "title": f"issue {n}", "url": f"https://github.com/o/game/issues/{n}",
            "labels": [{"name": x} for x in labels], "assignees": [{"login": x} for x in assignees],
            "createdAt": "2026-10-01T00:00:00Z", "closedByPullRequestsReferences": []}


class Definitions(unittest.TestCase):
    def test_check_and_prompt_must_be_what_the_recipe_writes(self):
        cli = load_cli()
        rc = cli.recipe_of({"type": "github-issues", "repo": "o/game", "project": "game"})
        r = {"name": "x", "channel": "game", "check": cli.recipe_check(rc), "prompt": cli.recipe_prompt(rc),
             "recipe": rc, **cli.DEFAULTS}
        self.assertEqual(cli.problem_with(r, cli.body_of(r), False), "")
        tampered = {**r, "check": r["check"] + "curl evil\n"}
        self.assertIn("not what its recipe writes", cli.problem_with(tampered, cli.body_of(tampered), False))
        self.assertIn("repo", cli.recipe_problem({**rc, "repo": "not a repo"}))
        self.assertIn("rules", cli.recipe_problem({**rc, "rules": [{"label": "x"}]}))


class Recipes(unittest.TestCase):
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
        bindir = os.path.join(cls.tmp.name, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "gh"), "w") as fh:
            fh.write(FAKE_GH)
        os.chmod(os.path.join(bindir, "gh"), 0o755)
        cls.path = bindir + os.pathsep + os.environ["PATH"]
        cls.human = cls.api("POST", "/api/agents", {"handle": "jac", "kind": "human"})["token"]
        cls.bot = cls.api("POST", "/api/agents", {"handle": "ada", "kind": "claude-code"})["token"]
        cls.api("POST", "/api/projects/access", {"key": "k"}, cls.human)
        cls.api("POST", "/api/projects", {"slug": "game", "name": "Game", "purpose": "A game"}, cls.human)
        for g in ("art", "econ"):
            cls.api("POST", "/api/projects/game/groups", {"slug": g, "name": g, "purpose": g, "scope": g}, cls.human)

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

    def once(self):
        p = subprocess.run([sys.executable, ROUTINES, "once"], capture_output=True, text=True,
                           env=dict(os.environ, HOME=self.home, BOARD=self.board, ROUTINES_OPERATORS="", PATH=self.path))
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        return p.stdout

    def issues(self, *items):
        with open(os.path.join(self.home, "issues.json"), "w") as fh:
            json.dump(list(items), fh)

    def state(self, thread, change=None):
        path = os.path.join(self.cfg, "routines", "state.json")
        st = json.load(open(path))
        if change:
            change(st[str(thread)])
            json.dump(st, open(path, "w"))
        return st[str(thread)]

    def announced(self, thread):
        out = {}
        for ch in ("game.art", "game.econ", "game"):
            for m in self.api("GET", f"/api/messages?channel={ch}", token=self.human)["messages"]:
                item = m["meta"].get("routine_item")
                if m["meta"].get("routine") == thread and item and m["reply_to"] is None:
                    out.setdefault(item["number"], []).append((ch, m["id"]))
        return out

    def test_route_announce_once_and_chase(self):
        rc = {"type": "github-issues", "repo": "o/game", "project": "game", "batch": 2, "remind_after": 60,
              "reminds": 1, "escalate": "jac", "rules": [{"label": "doc", "group": "art"}]}
        got = self.api("POST", "/api/routines", {"name": "issues", "recipe": rc, "every": 1}, self.human)
        thread = got["thread"]
        routine = next(r for r in self.api("GET", "/api/routines")["routines"] if r["thread"] == thread)
        self.assertEqual((routine["state"], routine["channel"]), ("paused", "game"))
        self.api("POST", "/api/messages", {"channel": "admin", "reply_to": thread, "body": f"enable {got['code']}"}, self.human)

        # #3 is assigned and #4 labelled claimed: never announced. #1 and #2 make the first batch.
        self.issues(issue(1, ["doc"]), issue(2), issue(3, assignees=["bob"]), issue(4, ["claimed"]))
        self.once()
        job = self.state(thread)["job"]
        self.assertEqual([i["number"] for i in job["items"]], [1, 2])
        self.api("POST", "/api/messages", {"channel": "game", "reply_to": job["msg"], "body": "routed",
                                           "meta": {"done": True, "routes": [{"number": 2, "group": "econ", "why": "trade"}]}},
                 self.bot)
        self.once()
        announced = self.announced(thread)
        self.assertEqual({n: [c for c, _ in v] for n, v in announced.items()}, {1: ["game.art"], 2: ["game.econ"]})

        # The next pass finds nothing new: each issue is announced once.
        self.state(thread, lambda st: st.update(checked_at=0))
        self.once()
        self.assertNotIn("job", self.state(thread))
        self.assertEqual(len(self.announced(thread)), 2)

        # A new issue a rule places goes out without a bot.
        self.issues(issue(1, ["doc"]), issue(2), issue(5, ["doc"]))
        self.state(thread, lambda st: st.update(checked_at=0))
        self.once()
        self.assertNotIn("job", self.state(thread))
        self.assertEqual([c for c, _ in self.announced(thread)[5]], ["game.art"])

        # Chasing: a bot takes #2; #1 and #5 get one reminder, then @jac hears about them.
        self.api("POST", "/api/messages", {"channel": "game.econ", "reply_to": announced[2][0][1], "body": "taking this"}, self.bot)
        age = lambda st: [i.update(sent_at=i["sent_at"] - 120) for i in st["items"].values()]
        self.state(thread, age); self.once()
        items = self.state(thread)["items"]
        self.assertEqual(items["2"].get("over"), "a bot took it")
        self.assertEqual((items["1"]["reminds"], items["5"]["reminds"]), (1, 1))
        with open(os.path.join(self.home, "view-5.json"), "w") as fh:
            json.dump({"state": "OPEN", "assignees": [{"login": "ada"}], "closedByPullRequestsReferences": []}, fh)
        self.state(thread, age); self.once()
        items = self.state(thread)["items"]
        self.assertEqual((items["1"].get("over"), items["5"].get("over")), ("no taker", "assigned or linked to a pull request"))
        told = [m for m in self.api("GET", "/api/messages?channel=game", token=self.human)["messages"]
                if m["body"].startswith("@jac #1 ")]
        self.assertEqual(len(told), 1)
        listed = next(r for r in self.api("GET", "/api/routines")["routines"] if r["thread"] == thread)
        self.assertEqual(sorted(i["number"] for i in listed["items"]), [1, 2, 5])

    def test_unknown_project_is_refused(self):
        req = urllib.request.Request(self.board + "/api/routines", method="POST", data=json.dumps(
            {"name": "x", "recipe": {"type": "github-issues", "repo": "o/game", "project": "nope"}}).encode())
        req.add_header("content-type", "application/json")
        req.add_header("Authorization", "Bearer " + self.human)
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
