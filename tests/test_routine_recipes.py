"""Recipe routines: GitHub or Forgejo issues routed to a workspace's channels, announced once, chased.

End to end like test_routines_test.py: the real board under uvicorn, bin/routines
as a subprocess, a fake `gh` on PATH that answers from files in $HOME, and a fake
Forgejo (#401) in a thread that answers from files in $HOME the same way.
Run with python -m unittest discover -s tests.
"""
import hashlib, http.server, importlib.machinery, importlib.util, json, os, re, socket, subprocess, sys, tempfile, threading, time, unittest, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTINES = str(ROOT / "bin" / "routines")
FAKE_GH = """#!/bin/sh
# gh issue list ... -> $HOME/issues.json ; gh issue view N ... -> $HOME/view-N.json or open and untaken
if [ "$1" = api ]; then echo bots; exit 0; fi     # gh api user: the account every bot shares
if [ "$2" = list ]; then cat "$HOME/issues.json"; exit 0; fi
if [ -f "$HOME/view-$3.json" ]; then cat "$HOME/view-$3.json"; exit 0; fi
echo '{"state": "OPEN", "assignees": [], "closedByPullRequestsReferences": []}'
"""


def load_cli():
    loader = importlib.machinery.SourceFileLoader("routines_cli", ROUTINES)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def age(st, secs=120):
    """Every announced item, as if announced and last looked at `secs` earlier."""
    for i in st["items"].values():
        i.update(sent_at=i["sent_at"] - secs, looked_at=i.get("looked_at", i["sent_at"]) - secs)


def issue(n, labels=(), assignees=()):
    return {"number": n, "title": f"issue {n}", "url": f"https://github.com/o/game/issues/{n}",
            "labels": [{"name": x} for x in labels], "assignees": [{"login": x} for x in assignees],
            "createdAt": "2026-10-01T00:00:00Z", "closedByPullRequestsReferences": []}


def fissue(n, labels=(), assignees=(), state="open", created="2026-10-01T00:00:00+00:00"):
    """An issue as Forgejo's API lists it: html_url, created_at with a zone, no linked PRs."""
    return {"number": n, "title": f"issue {n}", "html_url": f"http://forge.test/o/game/issues/{n}", "state": state,
            "labels": [{"name": x} for x in labels], "assignees": [{"login": x} for x in assignees],
            "created_at": created, "updated_at": created}


class FakeForgejo(http.server.BaseHTTPRequestHandler):
    """Gitea's API for o/game, from files in HOME: forgejo-issues.json and forgejo-pulls.json
    (lists), forgejo-view-N.json (one issue, else open and untaken), forgejo-fail (an HTTP
    status to answer everything with). Pages are two long, so every list exercises the
    Link header; the token must be `fleet-token`."""
    home = ""
    TOKEN = "fleet-token"

    def log_message(self, *a):
        pass

    def reply(self, code, body, link=""):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if link:
            self.send_header("Link", link)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        fail = os.path.join(self.home, "forgejo-fail")
        if os.path.exists(fail):
            return self.reply(int(open(fail).read().strip()), {"message": "no"})
        if self.headers.get("Authorization") != "token " + self.TOKEN:
            return self.reply(401, {"message": "token required"})
        if u.path == "/api/v1/user":
            return self.reply(200, {"login": "fleet"})
        m = re.fullmatch(r"/api/v1/repos/o/game/(issues|pulls)", u.path)
        if m:
            name = m.group(1) + ("-closed" if m.group(1) == "pulls" and q.get("state") == ["closed"] else "")
            try:
                items = json.load(open(os.path.join(self.home, f"forgejo-{name}.json")))
            except OSError:
                items = []
            limit, page = min(int(q.get("limit", ["50"])[0]), 2), int(q.get("page", ["1"])[0])
            more = page * limit < len(items)
            return self.reply(200, items[(page - 1) * limit:page * limit],
                              f'<{self.path}>; rel="next",<{self.path}>; rel="last"' if more else "")
        m = re.fullmatch(r"/api/v1/repos/o/game/issues/(\d+)", u.path)
        if m:
            path = os.path.join(self.home, f"forgejo-view-{m.group(1)}.json")
            if os.path.exists(path):
                return self.reply(200, json.load(open(path)))
            return self.reply(200, fissue(int(m.group(1))))
        self.reply(404, {"message": "not found"})


class Definitions(unittest.TestCase):
    def test_check_and_prompt_must_be_what_the_recipe_writes(self):
        cli = load_cli()
        rc = cli.recipe_of({"type": "github-issues", "repo": "o/game", "workspace": "game"})
        r = {"name": "x", "channel": "game", "check": cli.recipe_check(rc), "prompt": cli.recipe_prompt(rc),
             "recipe": rc, "recipe_version": cli.RECIPE_VERSION, **cli.DEFAULTS}
        self.assertEqual(cli.problem_with(r), "")
        tampered = {**r, "check": r["check"] + "curl evil\n"}
        self.assertIn("not what its recipe writes", cli.problem_with(tampered))
        unknown = {**r, "recipe_version": cli.RECIPE_VERSION + 1}
        self.assertIn("recipe_version", cli.problem_with(unknown))

    def test_a_release_that_changes_the_writer_leaves_older_routines_running(self):
        cli = load_cli()
        rc = cli.recipe_of({"type": "github-issues", "repo": "o/game", "workspace": "game"})
        old = {"name": "x", "channel": "game", "check": cli.recipe_check(rc).replace("1000", "500"),
               "prompt": cli.recipe_prompt(rc), "recipe": rc, "recipe_version": 1, **cli.DEFAULTS}
        cli.RECIPE_VERSION = 2   # as after a release whose recipe_check writes something else
        self.assertEqual(cli.problem_with(old), "")

    # What recipe_check and recipe_prompt write for one fixed recipe, per RECIPE_VERSION (#242).
    WRITER = {5: "89b05609a7b8f8a5d7e7e230c344874340d3d38d374cc6d95548c2c2f5889be0",
              6: "89b05609a7b8f8a5d7e7e230c344874340d3d38d374cc6d95548c2c2f5889be0",
              7: "fbf2c26d22ecb05fdf4509d3b9e8a87b3d904a9674f1e9af8c8e6e83c48e9b1f",
              8: "cad64a2c1268fccd19615e43f598a4046e569132c8923e0a7ae80afdaaa5b463",
              9: "cad64a2c1268fccd19615e43f598a4046e569132c8923e0a7ae80afdaaa5b463"}     # a GitHub recipe does not sweep: its writer is v8's
    FORGEJO_WRITER = {6: "26d230031cda07a732e6ce9ddc6b58a9eb79e2ffffb6d8cf47e3e1377a9619b7",
                      7: "64dbc569b8ae89d55355ae8664ab67deefe12f39297e479f6c70020a2c2e48b6",
                      8: "e739ed0d3b936258da64bbe2e0bf3dbc1be129ac0b64b97a77328c16e1ee72a6",
                      9: "a60299e9e4750b09838d9d7f9f4c8eb1cf499173022570d2c6dbc22276d182f9"}

    def test_the_recipe_writer_does_not_change_without_a_new_recipe_version(self):
        cli = load_cli()
        for kind, writer in (("github-issues", self.WRITER), ("forgejo-issues", self.FORGEJO_WRITER)):
            with self.subTest(kind=kind):
                rc = cli.recipe_of({"type": kind, "forgejo": "http://forge.test:3000", "repo": "o/game", "workspace": "game",
                                    "rules": [{"label": "doc", "channel": "art"}]})
                got = hashlib.sha256((cli.recipe_check(rc) + "\0" + cli.recipe_prompt(rc)).encode()).hexdigest()
                self.assertEqual(got, writer.get(cli.RECIPE_VERSION),
                                 f"recipe_check or recipe_prompt writes something new for {kind}: bump RECIPE_VERSION in bin/routines "
                                 f"(routines then get an upgrade to approve) and add {{{cli.RECIPE_VERSION}: \"{got}\"}} to the writer table")

    def test_a_forgejo_recipe_needs_the_forge_address_and_reads_it_with_the_fleet_token(self):
        # #401: the same settings as github-issues, plus `forgejo`; python3 and urllib only, no gh or curl
        cli = load_cli()
        rc = cli.recipe_of({"type": "forgejo-issues", "forgejo": "http://forge.test:3000/", "repo": "o/game", "workspace": "game"})
        self.assertEqual(rc["forgejo"], "http://forge.test:3000")
        self.assertEqual(cli.recipe_problem(rc), "")
        self.assertIn("forgejo", cli.recipe_problem({**rc, "forgejo": ""}))
        self.assertIn("forgejo", cli.recipe_problem({**rc, "forgejo": "forge.test"}))
        self.assertIn("type", cli.recipe_problem({**rc, "type": "gitlab-issues"}))
        gh = cli.recipe_of({"type": "github-issues", "repo": "o/game", "workspace": "game"})
        self.assertEqual((gh["forgejo"], cli.recipe_problem(gh)), ("", ""))
        check = cli.recipe_check(rc)
        self.assertTrue(check.startswith("#!/usr/bin/env python3"))
        for word in ("gh ", "curl"):
            self.assertNotIn(word, check)
        for word in ("FORGEJO_TOKEN", "/issues?state=open&type=issues", 'rel="next"', "502, 503, 504"):
            self.assertIn(word, check)
        prompt = cli.recipe_prompt(rc)
        self.assertIn("the runner posts each new Forgejo issue of o/game once, in the General channel", prompt)
        self.assertNotIn("gh issue view", prompt)
        self.assertIn("on http://forge.test:3000", cli.recipe_summary(rc))
        r = {"name": "x", "channel": "game", "check": check, "prompt": prompt, "recipe": rc,
             "recipe_version": cli.RECIPE_VERSION, **cli.DEFAULTS}
        self.assertEqual(cli.problem_with(r), "")
        # a github recipe stored before `forgejo` existed is read in the current shape (#242)
        old = {k: v for k, v in gh.items() if k != "forgejo"}
        up = cli.upgraded({**r, "recipe": old})
        self.assertEqual(up["recipe"]["forgejo"], "")

    def test_a_version_6_recipe_with_the_retired_settings_still_runs(self):
        # #663: remind_after, reminds, escalate and stale_after do nothing now; a stored recipe may still hold them
        cli = load_cli()
        rc = cli.recipe_of({"type": "github-issues", "repo": "o/game", "workspace": "game"})
        old = {**rc, "remind_after": 7200, "reminds": 3, "escalate": "jac", "stale_after": 86400}
        r = {"name": "x", "channel": "game", "check": "c", "prompt": "p", "recipe": old, "recipe_version": 6, **cli.DEFAULTS}
        self.assertEqual(cli.problem_with(r), "")
        up = cli.upgraded(r)
        self.assertEqual(sorted(set(old) - set(up["recipe"])), ["escalate", "remind_after", "reminds", "stale_after"])
        self.assertEqual(cli.problem_with({**up, "recipe_version": 6}), "")

    def test_the_owner_handle_is_quoted_whole_in_both_checks(self):
        # #666: a label such as bot:a/b must ask the board for that handle, not for the path /api/agents/a/b
        cli = load_cli()
        for kind in ("github-issues", "forgejo-issues"):
            rc = cli.recipe_of({"type": kind, "forgejo": "http://forge.test:3000", "repo": "o/game", "workspace": "game"})
            self.assertIn('urllib.parse.quote(h, safe="")', cli.recipe_check(rc), kind)
            self.assertNotIn("urllib.parse.quote(h)", cli.recipe_check(rc), kind)

    def test_a_check_that_finds_work_without_a_list_of_items_leaves_the_watermark(self):
        # #666: a hand-edited check must not advance the watermark over issues nothing announced
        cli = load_cli()
        rc = cli.recipe_of({"type": "github-issues", "repo": "o/game", "workspace": "game"})
        run = cli.Runner.__new__(cli.Runner)
        posted = []
        run.board = type("B", (), {"post": lambda self, *a, **k: posted.append(a)})()
        run.announce = lambda *a, **k: posted.append("announced")
        run.log = lambda *a, **k: None
        out = {"work": True, "watermark": {"sent": [1, 2]}, "found": ["#1 x"]}
        cli.run_check = lambda *a, **k: out
        st = {"watermark": {"sent": []}}
        got = run.check({"check": "c", "name": "n", "recipe": rc}, st)
        self.assertIn("returned no list of items", got)
        self.assertEqual((st["watermark"], posted), ({"sent": []}, []))

    def test_after_is_kept_in_utc(self):
        cli = load_cli()
        rc = cli.recipe_of({"type": "github-issues", "repo": "o/game", "workspace": "game",
                            "after": "2026-10-14T23:00:00+02:00"})
        self.assertEqual(rc["after"], "2026-10-14T21:00:00Z")
        self.assertEqual(cli.recipe_problem(rc), "")
        self.assertIn("after", cli.recipe_problem({**rc, "after": "yesterday"}))
        self.assertIn("repo", cli.recipe_problem({**rc, "repo": "not a repo"}))
        self.assertIn("rules", cli.recipe_problem({**rc, "rules": [{"label": "x"}]}))
        before = cli.recipe_of({"type": "github-issues", "repo": "o/game", "project": "game",
                                "rules": [{"label": "ui", "group": "design"}]})   # written before workspaces
        self.assertEqual((before["workspace"], before["rules"]), ("game", [{"label": "ui", "channel": "design"}]))
        self.assertEqual(cli.recipe_problem(before), "")

    def test_a_recipe_stored_before_a_rename_or_a_new_setting_still_runs(self):
        cli = load_cli()
        rc = cli.recipe_of({"type": "github-issues", "repo": "o/game", "workspace": "game"})
        old = {k: v for k, v in rc.items() if k != "workspace"}
        old.update(project="game", rules=[{"label": "ui", "group": "design"}],
                   escalate="jac", reminds=3, remind_after=7200, stale_after=86400)    # written before workspaces, with settings since retired
        r = {"name": "x", "channel": "game", "check": "c", "prompt": "p", "recipe": old, "recipe_version": 1, **cli.DEFAULTS}
        self.assertIn("not a complete recipe", cli.problem_with(r))
        up = cli.upgraded(r)
        self.assertEqual(cli.problem_with(up), "")
        self.assertEqual((up["recipe"]["workspace"], "escalate" in up["recipe"], up["recipe"]["rules"]),
                         ("game", False, [{"label": "ui", "channel": "design"}]))
        self.assertEqual(r["recipe"], old, "the stored definition is left as it was")
        odd = cli.upgraded({**r, "recipe": {**old, "surprise": 1}})     # a key no release wrote
        self.assertIn("not a complete recipe", cli.problem_with(odd))


class Recipes(unittest.TestCase):
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
        bindir = os.path.join(cls.tmp.name, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "gh"), "w") as fh:
            fh.write(FAKE_GH)
        os.chmod(os.path.join(bindir, "gh"), 0o755)
        cls.path = bindir + os.pathsep + os.environ["PATH"]
        FakeForgejo.home = cls.home
        cls.forge_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeForgejo)
        threading.Thread(target=cls.forge_srv.serve_forever, daemon=True).start()
        cls.forgejo = f"http://127.0.0.1:{cls.forge_srv.server_address[1]}"
        cls.human = cls.api("POST", "/api/agents", {"handle": "jac", "kind": "human"})["token"]
        cls.bot = cls.api("POST", "/api/agents", {"handle": "ada", "kind": "claude-code"})["token"]
        cls.api("POST", "/api/workspaces", {"slug": "game", "name": "Game", "purpose": "A game"}, cls.human)
        for g in ("art", "econ"):
            cls.api("POST", "/api/workspaces/game/channels", {"slug": g, "name": g, "description": g, "focus": g}, cls.human)

    @classmethod
    def tearDownClass(cls):
        cls.forge_srv.shutdown()
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
                           env=dict(os.environ, HOME=self.home, BOARD=self.board, BOT_CONFIG=os.devnull, PATH=self.path,
                                    FORGEJO_TOKEN=FakeForgejo.TOKEN))
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

    def channel(self, ch):
        """Every message in a channel, page by page: a list is capped in bytes as well as in
        count, and on a slow run the routines of earlier tests have posted more reminders."""
        out, since = [], 0
        while True:
            got = self.api("GET", f"/api/messages?channel={ch}&limit=500&since={since}", token=self.human)
            out += got["messages"]
            if not got.get("has_more"):
                return out
            since = got["next_since"]

    def announced(self, thread):
        out = {}
        for ch in ("game.art", "game.econ", "game"):
            for m in self.channel(ch):
                item = m["meta"].get("routine_item")
                if m["meta"].get("routine") == thread and item and m["reply_to"] is None:
                    out.setdefault(item["number"], []).append((ch, m["id"]))
        return out

    def test_new_issues_are_posted_once_in_general_with_no_job_and_no_follow_up(self):
        # #663: the runner posts each new issue itself; no bot is asked to route it, and nothing chases it afterwards
        rc = {"type": "github-issues", "repo": "o/game", "workspace": "game", "batch": 2,
              "rules": [{"label": "doc", "channel": "art"}]}
        got = self.api("POST", "/api/routines", {"name": "issues", "recipe": rc, "every": 1}, self.human)
        thread = got["id"]
        routine = self.api("GET", f"/api/routines/{thread}")
        self.assertEqual((routine["state"], routine["channel"]), ("paused", "game"))
        self.assertIn("Nothing for a bot to do", routine["prompt"])
        self.api("POST", f"/api/routines/{thread}/enable", {}, self.human)

        # #3 is ada's (bot:ada, and ada is running) and #4 labelled claimed: never announced.
        # The bots' shared account assigned says nothing (#225); a person assigned (#6) has it. #1 and #2 make the first batch.
        self.api("GET", "/api/me", token=self.bot)     # ada is around
        self.issues(issue(1, ["doc"], assignees=["bots"]), issue(2), issue(3, ["bot:ada"]), issue(4, ["claimed"]),
                    issue(6, assignees=["jac"]))
        self.once()
        st = self.state(thread)
        self.assertNotIn("job", st)       # no job for a bot
        announced = self.announced(thread)
        self.assertEqual({n: [c for c, _ in v] for n, v in announced.items()}, {1: ["game.art"], 2: ["game"]})
        self.assertEqual(st["watermark"], {"sent": [1, 2]})

        # The next pass finds nothing new: each issue is announced once, and nothing is posted again later.
        for _ in range(3):
            self.state(thread, lambda st: st.update(checked_at=0, **{"items": {n: {**i, "sent_at": 0} for n, i in st["items"].items()}}))
            self.once()
        self.assertEqual({n: len(v) for n, v in self.announced(thread).items()}, {1: 1, 2: 1})
        self.assertEqual([m for m in self.channel("game") if "Still nobody" in m["body"] or m["body"].startswith("@jac #1 ")], [])
        self.assertEqual(self.api("GET", f"/api/routines/{thread}")["state"], "enabled", "a slow pickup is not a failure")

        # A new issue is announced on its own pass.
        self.issues(issue(1, ["doc"]), issue(2), issue(5, ["doc"]))
        self.state(thread, lambda st: st.update(checked_at=0))
        self.once()
        self.assertEqual([c for c, _ in self.announced(thread)[5]], ["game.art"])
        listed = self.api("GET", f"/api/routines/{thread}")
        self.assertEqual(sorted(i["number"] for i in listed["items"]), [1, 2, 5])
    def test_an_owner_that_is_not_running_frees_the_issue(self):
        rc = {"type": "github-issues", "repo": "o/game", "workspace": "game", "batch": 5}
        thread = self.api("POST", "/api/routines", {"name": "owners", "recipe": rc, "every": 1}, self.human)["id"]
        self.api("POST", f"/api/routines/{thread}/enable", {}, self.human)
        self.api("GET", "/api/me", token=self.bot)     # ada is around; ghost runs nowhere
        self.issues(issue(41, ["bot:ghost"]), issue(42, ["bot:ada"]))
        self.once()
        self.assertEqual(list(self.announced(thread)), [41])      # ghost stopped: #41 is free. ada runs: #42 is hers.
        body = next(m["body"] for m in self.channel("game") if m["body"].startswith("New issue to take: #41"))
        self.assertIn("--add-label bot:<your handle>", body)
        self.assertNotIn("not running", body, "no re-offer wording: it is a new issue to take")
    def test_an_issue_announced_before_is_not_offered_again_when_its_owner_stops(self):
        # #663: no re-offers. The board releases a stopped worker's thread; the feed does not post the issue again.
        rc = {"type": "github-issues", "repo": "o/game", "workspace": "game", "batch": 5,
              "rules": [{"label": "doc", "channel": "art"}]}
        thread = self.api("POST", "/api/routines", {"name": "orphans", "recipe": rc, "every": 1}, self.human)["id"]
        self.api("POST", f"/api/routines/{thread}/enable", {}, self.human)
        self.issues(issue(51, ["doc"]))
        self.once()
        self.assertEqual(len(self.announced(thread)[51]), 1)
        self.issues(issue(51, ["doc", "bot:ghost"]))      # ghost took it, then stopped
        for _ in range(2):
            self.state(thread, lambda st: st.update(checked_at=0)); self.once()
        self.assertEqual(len(self.announced(thread)[51]), 1)
        self.assertEqual(self.state(thread)["watermark"], {"sent": [51]})
    def test_a_version_2_watermark_still_reads(self):
        cli = load_cli()
        rc = cli.recipe_of({"type": "github-issues", "repo": "o/game", "workspace": "game"})
        with open(os.path.join(self.home, "issues.json"), "w") as fh:
            json.dump([issue(61), issue(62)], fh)
        env = dict(os.environ, HOME=self.home, PATH=os.path.join(self.tmp.name, "bin") + os.pathsep + os.environ["PATH"],
                   BOARD=self.board)
        out = subprocess.run(["sh", "-c", cli.recipe_check(rc)], env={**env, "ROUTINE_WATERMARK": "[61]"},
                             capture_output=True, text=True, cwd=self.tmp.name)
        got = json.loads(out.stdout)
        self.assertEqual([i["number"] for i in got["items"]], [62], out.stderr)
        self.assertEqual(got["watermark"], {"sent": [61, 62]})

    def run_with_failing_gh(self, said):
        cli = load_cli()
        rc = cli.recipe_of({"type": "github-issues", "repo": "o/game", "workspace": "game"})
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "gh"), "w") as fh:
                fh.write(f"#!/bin/sh\necho '{said}' >&2\nexit 1\n")
            os.chmod(os.path.join(tmp, "gh"), 0o755)
            return subprocess.run(["sh", "-c", cli.recipe_check(rc)], capture_output=True, text=True, cwd=tmp,
                                  env=dict(os.environ, HOME=self.home, BOARD=self.board, ROUTINE_WATERMARK="[61]",
                                           PATH=tmp + os.pathsep + os.environ["PATH"]))

    def test_github_out_of_reach_is_no_work_and_any_other_gh_error_still_fails(self):
        # #293: one connection error used to stop the routine until a human said yes again
        for said in ("error connecting to api.github.com", "HTTP 503 Service Unavailable", "net/http: TLS handshake timeout"):
            with self.subTest(said=said):
                out = self.run_with_failing_gh(said)
                self.assertEqual(out.returncode, 0, out.stderr)
                got = json.loads(out.stdout)
                self.assertFalse(got["work"])
                self.assertNotIn("watermark", got)      # a pass with no work leaves the runner's as it is
        # not a passing trouble: a missing repo also says "could not resolve", and must stop the routine
        for said, shown in (("HTTP 401: Bad credentials", "Bad credentials"),
                            ("GraphQL: Could not resolve to a Repository with the name o/game. (repository)", "Could not resolve")):
            with self.subTest(said=said):
                out = self.run_with_failing_gh(said)
                self.assertEqual(out.returncode, 1, out.stdout)
                self.assertIn(shown, out.stderr)

    def forgejo_files(self, issues=None, pulls=None, views=(), closed=None):
        for name, items in (("issues", issues), ("pulls", pulls), ("pulls-closed", closed)):
            with open(os.path.join(self.home, f"forgejo-{name}.json"), "w") as fh:
                json.dump(items or [], fh)
        for v in views:
            with open(os.path.join(self.home, f"forgejo-view-{v['number']}.json"), "w") as fh:
                json.dump(v, fh)

    def test_forgejo_issues_are_announced_once_in_general_with_no_job(self):
        # #401, #663: the github-issues behaviour against Forgejo's API, through the fake in this process
        rc = {"type": "forgejo-issues", "forgejo": self.forgejo, "repo": "o/game", "workspace": "game", "batch": 5,
              "rules": [{"label": "doc", "channel": "art"}]}
        thread = self.api("POST", "/api/routines", {"name": "forge", "recipe": rc, "every": 1}, self.human)["id"]
        self.assertIn("Nothing for a bot to do: the runner posts each new Forgejo issue", self.api("GET", f"/api/routines/{thread}")["prompt"])
        self.api("POST", f"/api/routines/{thread}/enable", {}, self.human)
        self.api("GET", "/api/me", token=self.bot)     # ada is around; ghost runs nowhere
        # #1 a rule places; #2 needs a bot; #3 is ada's; #4 is labelled claimed; #5 has a person; #6 has an open
        # PR whose body references it; #7 is ghost's, who stopped; #8 is the shared account's, which says nothing
        self.forgejo_files(
            issues=[fissue(1, ["doc"]), fissue(2), fissue(3, ["bot:ada"]), fissue(4, ["claimed"]), fissue(5, assignees=["jac"]),
                    fissue(6), fissue(7, ["bot:ghost"]), fissue(8, assignees=["fleet"])],
            pulls=[{"number": 9, "state": "open", "body": "Closes #6.\n\nBoard thread: x"}])
        self.once()
        st = self.state(thread)
        self.assertNotIn("job", st)
        self.assertEqual(st["watermark"]["sent"], [1, 2, 7, 8])
        self.assertEqual(sorted(st["watermark"]["posted"]), ["1", "2", "7", "8"], "when each was posted, for the sweep (#795)")
        announced = self.announced(thread)
        self.assertEqual({n: [c for c, _ in v] for n, v in announced.items()}, {1: ["game.art"], 2: ["game"], 7: ["game"], 8: ["game"]})
        body = self.api("GET", f"/api/messages/{announced[7][0][1]}", token=self.human)["message"]["body"]
        self.assertIn("http://forge.test/o/game/issues/7", body)
        self.assertIn("labels the issue as its own on Forgejo: `bot:<your handle>` and `thread:<this thread's id>`", body)
        self.assertIn("`POST /repos/o/game/issues/7/labels`", body)
        self.assertNotIn("gh issue", body)
        self.assertNotIn("Its owner", body, "a stopped owner's issue is free, not a re-offer")
        # the next passes find nothing new; nothing is posted again, and nothing is looked at on Forgejo but the list
        for _ in range(2):
            self.state(thread, lambda st: st.update(checked_at=0)); self.once()
        self.assertEqual({n: len(v) for n, v in self.announced(thread).items()}, {1: 1, 2: 1, 7: 1, 8: 1})
    def run_forgejo_check(self, env=None):
        cli = load_cli()
        rc = cli.recipe_of({"type": "forgejo-issues", "forgejo": self.forgejo, "repo": "o/game", "workspace": "game"})
        return subprocess.run([sys.executable, "-c", cli.recipe_check(rc).split("\n", 1)[1]], capture_output=True, text=True,
                              cwd=self.tmp.name, env={**os.environ, "HOME": self.home, "BOARD": self.board, "ROUTINE_WATERMARK": "[61]",
                                                      "FORGEJO_TOKEN": FakeForgejo.TOKEN, **(env or {})})


    # ---- the sweep (#795): what nobody did with an issue that was announced

    def sweep_check(self, watermark):
        out = self.run_forgejo_check({"ROUTINE_WATERMARK": json.dumps(watermark)})
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def thread(self, complete):
        """A thread on the board by jac, complete or not: the issue label `thread:<id>` points at it."""
        root = self.api("POST", "/api/messages", {"channel": "game", "body": "take issue"}, self.human)["message"]
        if complete:
            self.api("POST", f"/api/threads/{root['id']}/complete", {"through": root["id"]}, self.human)
        return root["id"]

    def test_the_sweep_reposts_nudges_and_lists_by_its_rules(self):
        self.api("GET", "/api/me", token=self.bot)         # ada runs
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        done, open_ = self.thread(True), self.thread(False)
        recent = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 2 * 86400))
        fresh = lambda n, labels=(): {**fissue(n, labels), "updated_at": now}
        self.forgejo_files(
            issues=[fissue(71), fissue(72), fresh(73), fissue(74, ["needs:jac"]),
                    fissue(75, ["bot:ada", f"thread:{done}"]), fissue(76, ["bot:ada", f"thread:{open_}"]),
                    {**fissue(77, ["bot:ada", f"thread:{done}"]), "updated_at": now}, fissue(78), fissue(79), fissue(81, ["claimed"]),
                    fissue(82, ["needs:jac @all"]), fissue(83, ["needs:nobody-here"]), fissue(84, ["needs:ada"])],
            pulls=[{"number": 90, "state": "open", "body": "Closes #79."}],
            closed=[{"number": 91, "state": "closed", "merged": True, "body": "Closes #78.\n\nBoard thread: x"},
                    {"number": 92, "state": "closed", "merged": False, "body": "Closes #71."}])
        sent = [71, 72, 73, 74, 75, 76, 77, 78, 79, 81, 82, 83, 84]
        got = self.sweep_check({"sent": sent, "posted": {"72": recent}})
        kinds = {(i["number"], i["kind"]) for i in got["items"] if "kind" in i}
        self.assertEqual(kinds, {(71, "repost"), (74, "repost"), (75, "nudge"), (78, "finished"), (82, "repost"), (83, "repost"), (84, "repost")},
                         "free and quiet reposts (a closed unmerged PR does not finish it); 72 was posted 2 days ago, 73 and 77 are not quiet, "
                         "76's thread is not complete, 79 has an open PR, 81 is claimed; 78 is finished, so it is not offered as free")
        by = {i["number"]: i for i in got["items"] if "kind" in i}
        self.assertEqual(by[74].get("needs"), "jac")
        self.assertNotIn("needs", by[71])
        for n in (82, 83, 84):     # not a handle, a handle the board does not know, a bot: all are plain free issues
            self.assertNotIn("needs", by[n], n)
        self.assertEqual((by[75]["owner"], by[75]["thread"]), ("ada", done))
        mark = got["watermark"]
        self.assertEqual((sorted(mark["posted"]), sorted(mark["nudged"]), sorted(mark["listed"])), (["71", "72", "74", "82", "83", "84"], ["75"], ["78"]))
        # the next pass with that watermark does none of it again
        again = self.sweep_check(mark)
        self.assertEqual([i for i in again["items"] if "kind" in i], [])

    def test_the_sweep_posts_where_it_should(self):
        self.api("GET", "/api/me", token=self.bot)
        rc = {"type": "forgejo-issues", "forgejo": self.forgejo, "repo": "o/game", "workspace": "game", "batch": 5}
        thread = self.api("POST", "/api/routines", {"name": "sweep", "recipe": rc, "every": 1}, self.human)["id"]
        self.assertEqual(self.api("GET", f"/api/routines/{thread}")["recipe"]["stale_days"], 3)
        self.api("POST", f"/api/routines/{thread}/enable", {}, self.human)
        done = self.thread(True)
        self.forgejo_files(issues=[fissue(71), fissue(74, ["needs:jac"]), fissue(75, ["bot:ada", f"thread:{done}"]), fissue(78)],
                           closed=[{"number": 91, "state": "closed", "merged": True, "body": "Fixes #78"}])
        # the routine has announced these before: a watermark that says so, and nothing posted since
        self.once()                                                    # first pass announces the new ones: 71, 74, 78 (75 is ada's)
        self.state(thread, lambda st: st.update(checked_at=0, watermark={"sent": [71, 74, 75, 78]}))
        self.once()
        general = self.channel("game")
        mine = [m for m in general if m["meta"].get("routine") == thread]
        again = [m for m in mine if m["body"].startswith("Still open and nobody has it: #71")]
        self.assertEqual(len(again), 1)
        self.assertIn("`bot:<your handle>`", again[0]["body"], "a free issue is posted like a new one")
        needs = [m for m in mine if m["body"].startswith("Waiting on @jac: #74")]
        self.assertEqual(len(needs), 1)
        self.assertEqual((needs[0]["meta"]["needs"], needs[0]["meta"]["ask"]), ("jac", "#74 issue 74: your decision"))
        listing = [m for m in mine if m["meta"].get("sweep") == "finished"]
        self.assertEqual(len(listing), 1)
        self.assertIn("#78", listing[0]["body"])
        self.assertNotIn("#71", listing[0]["body"])
        nudge = [m for m in self.api("GET", f"/api/threads/{done}", token=self.human)["messages"] if m["meta"].get("sweep") == "nudge"]
        self.assertEqual(len(nudge), 1)
        self.assertEqual(nudge[0]["reply_to"], done)
        self.assertIn("@ada #75", nudge[0]["body"])
        self.assertIn("take your label off", nudge[0]["body"])
        # and none of it again on the next pass
        before = len(self.channel("game"))
        self.state(thread, lambda st: st.update(checked_at=0)); self.once()
        self.assertEqual(len(self.channel("game")), before)

    def test_forgejo_out_of_reach_is_no_work_and_a_refused_token_or_missing_repo_stops_the_routine(self):
        self.forgejo_files(issues=[fissue(61), fissue(62), fissue(63)])
        out = self.run_forgejo_check()
        got = json.loads(out.stdout)
        self.assertEqual([i["number"] for i in got["items"] if "kind" not in i], [62, 63], out.stderr)     # three pages of two, one sent before
        self.assertEqual(got["watermark"]["sent"], [61, 62, 63])
        fail = os.path.join(self.home, "forgejo-fail")
        try:
            for code in (502, 503, 504):
                with self.subTest(code=code):
                    open(fail, "w").write(str(code))
                    out = self.run_forgejo_check()
                    self.assertEqual(out.returncode, 0, out.stderr)
                    got = json.loads(out.stdout)
                    self.assertFalse(got["work"])
                    self.assertNotIn("watermark", got)      # a pass with no work leaves the runner's as it is
            for code in (401, 403, 404):
                with self.subTest(code=code):
                    open(fail, "w").write(str(code))
                    out = self.run_forgejo_check()
                    self.assertEqual(out.returncode, 1, out.stdout)
                    self.assertIn(f"Forgejo answered {code} for /user", out.stderr)
        finally:
            os.remove(fail)
        out = self.run_forgejo_check({"FORGEJO_TOKEN": "wrong"})
        self.assertEqual(out.returncode, 1)
        self.assertIn("401", out.stderr)
        # nothing listening at the address: no work this pass, the routine keeps running
        cli = load_cli()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        rc = cli.recipe_of({"type": "forgejo-issues", "forgejo": f"http://127.0.0.1:{port}", "repo": "o/game", "workspace": "game"})
        out = subprocess.run([sys.executable, "-c", cli.recipe_check(rc).split("\n", 1)[1]], capture_output=True, text=True,
                             env=dict(os.environ, BOARD=self.board, FORGEJO_TOKEN="x"))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(json.loads(out.stdout)["work"])

    def test_a_workspace_that_goes_away_stops_the_routine_with_the_reason(self):
        self.api("POST", "/api/workspaces", {"slug": "gone", "name": "Gone", "purpose": "soon archived"}, self.human)
        rc = {"type": "github-issues", "repo": "o/game", "workspace": "gone", "rules": [{"label": "doc", "channel": "art"}]}
        got = self.api("POST", "/api/routines", {"name": "gone", "recipe": rc, "every": 1}, self.human)
        self.api("POST", f"/api/routines/{got['id']}/enable", {}, self.human)
        self.api("PATCH", "/api/workspaces/gone", {"archived": True, "archive_confirm": True}, self.human)
        self.issues(issue(40, ["doc"]))
        self.once()
        routine = self.api("GET", f"/api/routines/{got['id']}")
        self.assertEqual(routine["state"], "failed")
        self.assertIn("archived", routine["failure"])

    def test_unknown_workspace_is_refused(self):
        req = urllib.request.Request(self.board + "/api/routines", method="POST", data=json.dumps(
            {"name": "x", "recipe": {"type": "github-issues", "repo": "o/game", "workspace": "nope"}}).encode())
        req.add_header("content-type", "application/json")
        req.add_header("Authorization", "Bearer " + self.human)
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
