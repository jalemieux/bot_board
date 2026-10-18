"""bin/bot routes a pool bot's work by the board's project records.

Runs the real launcher against a small fake board and a fake `claude` that
records where each run happened and what it was told. Needs bash, git and
python3; no dependencies. `python3 tests/test_project_routing.py` or pytest.
"""
import datetime, json, os, shutil, signal, subprocess, sys, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT = os.path.join(ROOT, "bin", "bot")
ME = "tst"


def msg(mid, board, body, meta=None, mention=False, **kw):
    return {"id": mid, "board": board, "thread_id": None, "author": "jac", "body": body,
            "meta": meta or {}, "mention": mention, "reply_to": None,
            "mentions": [ME] if mention else [],
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), **kw}


def scope(slug, readiness="ready", body="", archived=False, **kw):
    return {"slug": slug, "channel": slug, "archived": archived, "readiness": readiness,
            "context": {"revision": 3 if body else None, "body": body, "sources": ["thread 12"] if body else []}, **kw}


class FakeBoard:
    """Messages, an inbox, and GET /api/channels/{slug}/project from a table."""

    def __init__(self, messages, routes, has_lookup=True, flaky=(), assigned=None, assignment_error=False,
                 cursor=0, page_size=20, backlog_failures=0, workers=None, claim_failures=0, personality=None):
        self.messages, self.routes, self.has_lookup = messages, routes, has_lookup
        self.flaky = dict.fromkeys(flaky, 1)   # channel -> lookups left that fail with 500
        self.lookups = []
        self.assigned = assigned
        self.assignment_error = assignment_error
        self.seed_routes = {}
        self.cursor, self.page_size, self.backlog_failures = cursor, page_size, backlog_failures
        self.requests = []
        self.workers = dict(workers or {})
        self.claim_failures = claim_failures
        self.claim_lock = threading.Lock()
        self.tokens = {"t0k": ME}
        self.on_claim = None
        self.personality = personality
        self.profile_failure = False
        board = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def send(self, code, data):
                b = json.dumps(data, separators=(",", ":")).encode()   # compact, as the board sends it
                self.send_response(code); self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or "{}")
                if self.path == "/api/agents":
                    token = payload["handle"] + "-token"
                    board.tokens[token] = payload["handle"]
                    return self.send(200, {"token": token})
                if self.path.startswith("/api/threads/") and self.path.endswith("/worker"):
                    tid = int(self.path.split("/")[3])
                    handle = board.tokens[self.headers["Authorization"].removeprefix("Bearer ")]
                    with board.claim_lock:
                        board.requests.append("POST " + self.path)
                        if board.claim_failures:
                            board.claim_failures -= 1
                            return self.send(503, {"detail": "claim temporarily unavailable"})
                        worker = board.workers.setdefault(tid, handle)
                        if worker != handle:
                            return self.send(409, {"detail": "already claimed"})
                        if board.on_claim:
                            hook, board.on_claim = board.on_claim, None
                            hook()
                        return self.send(200, {"thread_id": tid, "worker": worker})
                return self.send(200, {"message": {"id": 999}})

            def do_GET(self):
                u = urlparse(self.path); q = parse_qs(u.query); path = u.path
                board.requests.append(self.path)
                handle = board.tokens.get(self.headers.get("Authorization", "").removeprefix("Bearer "), ME)
                since = int(q.get("since", ["0"])[0])
                if path == "/api/me":
                    if board.profile_failure: return self.send(503, {"detail": "profile unavailable"})
                    agent = {"handle": handle}
                    if board.personality is not None: agent["personality"] = board.personality
                    return self.send(200, {"agent": agent, "cursor": board.cursor})
                if path == '/api/me/project-pool':
                    if not board.has_lookup: return self.send(404, {'detail': 'Not Found'})
                    if board.assignment_error: return self.send(500, {'detail': 'failed'})
                    assigned = board.assigned
                    if isinstance(assigned, list):
                        assigned = board.assigned.pop(0) if len(board.assigned) > 1 else board.assigned[0]
                    return self.send(200, {'project': assigned})
                if path in ("/api/agents", "/api/channels"): return self.send(200, {"agents": [], "channels": []})
                if path.startswith('/api/threads/') and path.endswith('/seed'):
                    tid=path.split('/')[3]
                    return self.send(200, board.seed_routes[tid]) if tid in board.seed_routes else self.send(404, {'detail':'Not Found'})
                if path.startswith("/api/threads/"):
                    tid = int(path.split("/")[3])
                    messages = [dict(m, worker=board.workers.get(tid)) for m in board.messages
                                if (m["thread_id"] or m["id"]) == tid and m["id"] > since]
                    page = messages[:board.page_size]
                    return self.send(200, {"messages": page, "worker": board.workers.get(tid),
                                          "has_more": len(messages) > len(page),
                                          "next_since": page[-1]["id"] if page else since})
                if path in ("/api/messages", "/api/inbox"):
                    new = [dict(m, worker=board.workers.get(m["thread_id"] or m["id"]))
                           for m in board.messages if m["id"] > since and
                           (path == "/api/messages" or m["mention"] or
                            board.workers.get(m["thread_id"] or m["id"]) == handle)]
                    if q.get("order") == ["desc"]:
                        if board.backlog_failures:
                            board.backlog_failures -= 1
                            return self.send(500, {"detail": "backlog unavailable"})
                        before = int(q.get("before", [str(board.cursor + 1)])[0])
                        new = sorted((m for m in new if m["id"] < before),
                                     key=lambda m: m["id"], reverse=True)
                        page = new[:board.page_size]
                        return self.send(200, {"messages": page, "has_more": len(new) > len(page),
                                              "next_before": page[-1]["id"] if page else 0})
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
        self.srv.server_close()


FAKE_CLAUDE = """#!/usr/bin/env python3
import sys, os, json
a = sys.argv[1:]
sid = a[a.index("--session-id") + 1] if "--session-id" in a else a[a.index("--resume") + 1]
with open(os.environ["ROUTING_TEST_RUNS"], "a") as f:
    f.write(json.dumps({"cwd": os.getcwd(), "sid": sid, "prompt": a[-1], "repo_token": os.environ.get("GH_TOKEN"), "gh_config": os.environ.get("GH_CONFIG_DIR")}) + "\\n")
"""


class Routing(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="routing-"))
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

    def run_bot(self, board, until, timeout=40, watch=None):
        """Start the launcher, wait until until(runs) holds, stop it, return the runs."""
        args = [BOT, "claude", "--pool", self.pool, "--name", ME]
        if watch: args += ['--watch', watch]
        p = subprocess.Popen(args, env=dict(self.env, BOARD=board.url),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        runs, end = [], time.time() + timeout
        try:
            while time.time() < end:
                time.sleep(0.5)
                if os.path.exists(self.runs):
                    with open(self.runs) as f: runs = [json.loads(l) for l in f]
                if until(runs): break
                if p.poll() is not None: break
        finally:
            if p.poll() is None: os.killpg(p.pid, signal.SIGTERM)
            p.wait(10)
        with open(os.path.join(self.home, ".config/bot_board/bots", ME, "log")) as f: self.log = f.read()
        return [r for r in runs if "read the rules and return" not in r["prompt"]]   # skip the first turn

    def test_harness_uses_fleet_token_and_isolated_gh_configuration(self):
        os.makedirs(os.path.join(self.pool, 'wordsnap'))
        cfg = os.path.join(self.home, '.config', 'bot_board')
        os.makedirs(cfg)
        with open(os.path.join(cfg, 'fleet.env'), 'w') as f:
            f.write('# written by bin/keeper from the board\nGH_TOKEN=board-test-token\n')
        personal = os.path.join(self.home, '.config', 'gh')
        os.makedirs(personal)
        with open(os.path.join(personal, 'hosts.yml'), 'w') as f:
            f.write('github.com: personal-saved-account')
        self.env.update(GH_TOKEN='personal-inherited', GITHUB_TOKEN='personal-alias', GH_CONFIG_DIR=personal)
        board = FakeBoard([msg(1, 'wordsnap', 'repository access check')], {})
        try:
            runs = self.run_bot(board, lambda rs: any('repository access check' in r['prompt'] for r in rs))
        finally:
            board.close()
        task = next(r for r in runs if 'repository access check' in r['prompt'])
        self.assertEqual(task['repo_token'], 'board-test-token')
        self.assertNotEqual(task['gh_config'], personal)
        self.assertTrue(os.path.exists(os.path.join(personal, 'hosts.yml')))

    def test_managed_launcher_update_waits_for_the_active_harness(self):
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        managed = os.path.join(self.tmp, "managed")
        os.makedirs(managed)
        os.symlink(ROOT, os.path.join(managed, "launcher"))
        new = os.path.join(self.tmp, "new", "bin")
        os.makedirs(new)
        adopted = os.path.join(self.tmp, "adopted")
        with open(os.path.join(new, "bot"), "w") as f:
            f.write('#!/bin/bash\nprintf "%s\\n" "$@" > "$TEST_ADOPTED"\n')
        os.chmod(os.path.join(new, "bot"), 0o755)
        entered, release = os.path.join(self.tmp, "entered"), os.path.join(self.tmp, "release")
        harness = os.path.join(self.tmp, "bin", "claude")
        with open(harness, "a") as f:
            f.write("\nif 'upgrade boundary' in a[-1]:\n"
                    "    import time\n"
                    "    open(os.environ['TEST_ENTERED'], 'w').close()\n"
                    "    while not os.path.exists(os.environ['TEST_RELEASE']): time.sleep(.05)\n")
        self.env.update(KEEPER_MANAGED_ROOT=managed, TEST_ADOPTED=adopted,
                        TEST_ENTERED=entered, TEST_RELEASE=release)
        switched = False
        def until(runs):
            nonlocal switched
            if os.path.exists(entered) and not switched:
                os.unlink(os.path.join(managed, "launcher"))
                os.symlink(os.path.dirname(new), os.path.join(managed, "launcher"))
                time.sleep(.2)
                self.assertFalse(os.path.exists(adopted))
                open(release, 'w').close()
                switched = True
            return os.path.exists(adopted)
        board = FakeBoard([msg(42, "wordsnap", "upgrade boundary")], {})
        try:
            runs = self.run_bot(board, until)
        finally:
            board.close()
        self.assertTrue(switched)
        self.assertTrue(os.path.exists(adopted))
        with open(adopted) as f:
            self.assertEqual(f.read().splitlines(), ['claude', '--pool', self.pool, '--name', ME])
        state = os.path.join(self.home, '.config/bot_board/bots', ME)
        self.assertTrue(os.path.getsize(os.path.join(state, 'session.wordsnap')))
        self.assertTrue(os.path.getsize(os.path.join(self.home, '.config/bot_board', ME + '.token')))

    def test_personality_is_fetched_for_each_new_session_not_resumed_turns(self):
        for project in ("game", "other", "third"):
            os.makedirs(os.path.join(self.pool, project))
        board = FakeBoard([msg(1, "game", "first game task"), msg(2, "other", "first other task")],
                          {}, has_lookup=False, personality="Warm and quietly curious.")
        try:
            runs = self.run_bot(board, lambda rs: any("first other task" in r["prompt"] for r in rs))
            with open(self.runs) as f:
                first_runs = [json.loads(line) for line in f]
            for run in first_runs:
                self.assertEqual(run["prompt"].count("Warm and quietly curious."), 1)
                self.assertIn("Task instructions, project rules, safety", run["prompt"])
            self.assertEqual(len({r["sid"] for r in first_runs}), 3)
            board.personality = "Direct and kind."
            board.messages.extend([msg(3, "game", "resumed game task"), msg(4, "third", "fresh third task")])
            runs = self.run_bot(board, lambda rs: any("fresh third task" in r["prompt"] for r in rs))
        finally:
            board.close()
        resumed = next(r for r in runs if "resumed game task" in r["prompt"])
        self.assertNotIn("Board-owned communication style", resumed["prompt"])
        self.assertNotIn("Direct and kind.", resumed["prompt"])
        self.assertEqual(resumed["sid"], next(r["sid"] for r in first_runs if "first game task" in r["prompt"]))
        fresh = next(r for r in runs if "fresh third task" in r["prompt"])
        self.assertIn("Direct and kind.", fresh["prompt"])
        self.assertNotIn("Warm and quietly curious.", fresh["prompt"])
        self.assertLess(fresh["prompt"].index("Direct and kind."), fresh["prompt"].index("fresh third task"))

    def test_empty_personality_does_not_change_initial_prompt(self):
        os.makedirs(os.path.join(self.pool, "game"))
        board = FakeBoard([msg(1, "game", "empty style task")], {}, has_lookup=False, personality="")
        try:
            runs = self.run_bot(board, lambda rs: any("empty style task" in r["prompt"] for r in rs))
        finally:
            board.close()
        self.assertTrue(runs)
        self.assertNotIn("Board-owned communication style", "\n".join(r["prompt"] for r in runs))

    def test_profile_failure_holds_work_instead_of_starting_a_styleless_session(self):
        os.makedirs(os.path.join(self.pool, "game"))
        board = FakeBoard([msg(1, "game", "keep pending work")], {}, has_lookup=False, personality="Concise.")
        board.on_claim = lambda: setattr(board, "profile_failure", True)
        try:
            runs = self.run_bot(board, lambda rs: any("keep pending work" in r["prompt"] for r in rs))
        finally:
            board.close()
        self.assertEqual(runs, [])
        self.assertIn("initial bot profile lookup failed", self.log)
        state = os.path.join(self.home, ".config/bot_board/bots", ME)
        with open(os.path.join(state, "held")) as f:
            self.assertIn("keep pending work", f.read())
        self.assertFalse(os.path.exists(os.path.join(state, "session.game")))

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
        # Cached within routing, then checked again at each harness boundary.
        self.assertNotIn("help", board.lookups)
        self.assertLessEqual(board.lookups.count("game-art"), 2)

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
        self.assertNotIn("Board-owned communication style", "\n".join(r["prompt"] for r in runs))

    def test_another_launchers_notice_never_wakes_the_bot(self):
        # Two bots whose harness failed kept @mentioning each other in notices, for hours.
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        board = FakeBoard([
            msg(1, "wordsnap", "@tst my harness (claude) exited with code 1 before replying",
                meta={"launcher": "bin/bot"}, mention=True),
            msg(2, "wordsnap", "fix the popup")], {}, has_lookup=False)
        try:
            runs = self.run_bot(board, lambda rs: any("fix the popup" in r["prompt"] for r in rs))
        finally:
            board.close()
        self.assertTrue(any("fix the popup" in r["prompt"] for r in runs))
        self.assertFalse(any("exited with code" in r["prompt"] for r in runs))

    def test_dedicated_pool_filters_projects_and_group_work(self):
        os.makedirs(os.path.join(self.pool, 'game'))
        game = scope('game', repositories=[], bot_pool={'configured': True, 'bots': [ME]})
        other = scope('other', repositories=[], bot_pool={'configured': True, 'bots': ['someone']})
        board = FakeBoard([msg(1, 'game-art', 'our art task'), msg(2, 'other', 'not our project', mention=True),
            msg(3, 'legacy', 'legacy unrelated'), msg(4, 'help', 'fleet question')],
            {'game-art': {'project': game, 'group': scope('art')},
             'other': {'project': other, 'group': None}}, assigned='game')
        try:
            runs = self.run_bot(board, lambda rs: any('fleet question' in r['prompt'] for r in rs), watch='help')
        finally:
            board.close()
        prompts = '\n'.join(r['prompt'] for r in runs)
        self.assertIn('our art task', prompts)
        self.assertIn('fleet question', prompts)
        self.assertIn('not our project', prompts)  # addressed messages get a handoff response, not new work
        self.assertIn('outside your pool assignment', prompts)
        self.assertFalse(any(r['cwd'] == os.path.join(self.pool, 'other') for r in runs))
        self.assertNotIn('legacy unrelated', prompts)

    def test_shared_pool_bots_do_not_enter_another_project_pool(self):
        board = FakeBoard([msg(1, 'game', 'reserved work'), msg(2, 'help', 'fleet control'),
            msg(3, 'game', 'please hand this off', mention=True)],
            {'game': {'project': scope('game', bot_pool={'configured': True, 'bots': ['someone']}), 'group': None}})
        try:
            runs = self.run_bot(board, lambda rs: any('fleet control' in r['prompt'] for r in rs))
        finally:
            board.close()
        self.assertNotIn('reserved work', '\n'.join(r['prompt'] for r in runs))
        self.assertIn('please hand this off', '\n'.join(r['prompt'] for r in runs))

    def test_assignment_is_rechecked_between_runs(self):
        os.makedirs(os.path.join(self.pool, 'game'))
        os.makedirs(os.path.join(self.pool, 'other'))
        board = FakeBoard([msg(1, 'game', 'keep our work'), msg(2, 'other', 'stale queued work'),
            msg(3, 'help', 'boundary control')], {
            'game': {'project': scope('game'), 'group': None},
            'other': {'project': scope('other'), 'group': None}}, assigned=[None, 'game'])
        try:
            runs = self.run_bot(board, lambda rs: any('boundary control' in r['prompt'] for r in rs))
        finally:
            board.close()
        prompts = '\n'.join(r['prompt'] for r in runs)
        self.assertIn('keep our work', prompts)
        self.assertNotIn('stale queued work', prompts)

    def test_assignment_lookup_failure_holds_work_but_keeps_fleet_communication(self):
        board = FakeBoard([msg(1, 'game', 'do not guess'), msg(2, 'help', 'failure control')],
            {'game': {'project': scope('game'), 'group': None}}, assignment_error=True)
        try:
            runs = self.run_bot(board, lambda rs: any('failure control' in r['prompt'] for r in rs))
        finally:
            board.close()
        self.assertNotIn('do not guess', '\n'.join(r['prompt'] for r in runs))
        self.assertIn('pool assignment lookup failed', self.log)

    def test_help_bootstrap_runs_in_project_before_allocation(self):
        os.makedirs(os.path.join(self.pool, 'game'))
        game=scope('game', repositories=[], bot_pool={'configured':True, 'bots':['another']})
        art=scope('art', readiness='unseeded')
        board=FakeBoard([msg(1,'help','curate original art references',meta={'kind':'context_seed'}),
            msg(2,'game-art','do not build ships yet'),msg(3,'help','bootstrap control')],
            {'game-art':{'project':game,'group':art}},assigned='elsewhere')
        board.seed_routes['1']={'project':game,'group':art,'seed_thread_id':1,'completed':False}
        try:
            runs=self.run_bot(board,lambda rs:any('bootstrap control' in r['prompt'] for r in rs))
        finally:
            board.close()
        project_runs=[r for r in runs if r['cwd']==os.path.join(self.pool,'game')]
        self.assertTrue(any('curate original art references' in r['prompt'] for r in project_runs))
        self.assertIn('Bootstrap exception',project_runs[0]['prompt'])
        self.assertNotIn('do not build ships yet','\n'.join(r['prompt'] for r in runs))

    def test_new_capacity_catches_up_recent_roots_without_rewinding_live_cursor(self):
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=8)).isoformat()
        recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=6)).isoformat()
        board = FakeBoard([
            msg(1, "wordsnap", "too old", created_at=old),
            msg(2, "wordsnap", "recent unanswered task", created_at=recent),
            msg(3, "wordsnap", "context event", meta={"kind": "context_update"}),
            msg(4, "wordsnap", "launcher failure", meta={"launcher": "bin/bot"}),
            msg(5, "wordsnap", "already done", meta={"done": True}),
            msg(6, "wordsnap", "recent second task"),
            msg(7, "wordsnap", "new live request"),
        ], {}, has_lookup=False, cursor=6, page_size=2)
        try:
            runs = self.run_bot(board, lambda rs: any("new live request" in r["prompt"] for r in rs))
        finally:
            board.close()
        prompts = "\n".join(r["prompt"] for r in runs)
        for text in ("recent unanswered task", "recent second task", "new live request"):
            self.assertEqual(prompts.count(text), 1)
        for text in ("too old", "context event", "launcher failure", "already done"):
            self.assertNotIn(text, prompts)
        self.assertIn("[backlog: message 2", prompts)
        self.assertIn("read the current thread first", prompts)
        self.assertIn("No prose claims or back-off messages", prompts)
        self.assertIn("[channel: message 7", prompts)
        self.assertIn("seen=6", self.log)
        self.assertTrue(any("since=6" in path for path in board.requests))
        self.assertEqual([parse_qs(urlparse(path).query)["before"][0]
                          for path in board.requests if "order=desc" in path], ["7", "5", "3"])
        seen = os.path.join(self.home, ".config/bot_board/bots", ME, "seen")
        with open(seen) as f:
            self.assertEqual(f.read().strip(), "7")

    def test_restart_discovers_backlog_but_preserves_saved_cursor_and_watches(self):
        state = os.path.join(self.home, ".config/bot_board/bots", ME)
        os.makedirs(state)
        with open(os.path.join(self.home, ".config/bot_board", ME + ".token"), "w") as f:
            f.write("t0k")
        with open(os.path.join(state, "seen"), "w") as f:
            f.write("4")
        with open(os.path.join(state, "held"), "w") as f:
            f.write("2\twordsnap\t2\tjac\tbacklog\t\trestart pending request\n")
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        board = FakeBoard([msg(1, "unwatched", "do not take unrelated history"),
            msg(2, "wordsnap", "restart pending request"),
            msg(3, "lobby", "do not replay fleet chatter"),
            msg(4, "wordsnap", "own old post", author=ME)], {}, cursor=4)
        try:
            runs = self.run_bot(board, lambda rs: any("restart pending request" in r["prompt"] for r in rs),
                                watch="wordsnap")
        finally:
            board.close()
        prompts = "\n".join(r["prompt"] for r in runs)
        self.assertIn("[backlog: message 2", prompts)
        self.assertEqual(prompts.count("restart pending request"), 1)
        for text in ("do not take unrelated history", "do not replay fleet chatter", "own old post"):
            self.assertNotIn(text, prompts)
        with open(os.path.join(state, "seen")) as f:
            self.assertEqual(f.read(), "4")

    def test_backlog_uses_assignment_readiness_and_seed_routing(self):
        os.makedirs(os.path.join(self.pool, "game"))
        board = FakeBoard([
            msg(1, "help", "unclaimed bootstrap", meta={"kind": "context_seed"}),
            msg(2, "game-art", "do not do unseeded work"),
            msg(3, "reserved", "do not enter reserved pool"),
            msg(4, "retired", "do not reopen archived project"),
        ], {"game-art": {"project": scope("game"), "group": scope("art", readiness="unseeded")},
            "reserved": {"project": scope("reserved", bot_pool={"configured": True, "bots": ["another"]}),
                         "group": None},
            "retired": {"project": scope("retired", archived=True), "group": None}}, cursor=4)
        board.seed_routes["1"] = {"project": scope("game"), "group": scope("art", readiness="unseeded"),
                                  "seed_thread_id": 1, "completed": False}
        try:
            runs = self.run_bot(board, lambda rs: any("unclaimed bootstrap" in r["prompt"] for r in rs))
        finally:
            board.close()
        prompts = "\n".join(r["prompt"] for r in runs)
        self.assertIn("[backlog: message 1", prompts)
        self.assertIn("Bootstrap exception", prompts)
        self.assertTrue(any(r["cwd"] == os.path.join(self.pool, "game") for r in runs))
        for text in ("do not do unseeded work", "do not enter reserved pool", "do not reopen archived project"):
            self.assertNotIn(text, prompts)

    def test_backlog_discovery_failure_retries_without_losing_history(self):
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        board = FakeBoard([msg(1, "wordsnap", "recover backlog after failure")], {},
                          cursor=1, backlog_failures=1)
        try:
            runs = self.run_bot(board, lambda rs: any("recover backlog after failure" in r["prompt"] for r in rs))
        finally:
            board.close()
        self.assertIn("startup catch-up failed; retrying without advancing its cursor", self.log)
        self.assertEqual(sum("recover backlog after failure" in r["prompt"] for r in runs), 1)
        self.assertEqual(sum("order=desc" in path for path in board.requests), 2)

    def test_backlog_exact_seven_day_boundary_and_invalid_pagination(self):
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
        board = FakeBoard([
            msg(1, "wordsnap", "outside", created_at=(cutoff - datetime.timedelta(microseconds=1)).isoformat()),
            msg(2, "wordsnap", "boundary", created_at=cutoff.isoformat()),
            msg(3, "wordsnap", "inside", created_at=(cutoff + datetime.timedelta(microseconds=1)).isoformat()),
            msg(4, "wordsnap", "live only"),
        ], {}, cursor=3)
        with open(BOT) as f:
            source = f.read()
        function = source[source.index("fetch_backlog() {"):source.index("\nfetch_new() {")]
        token = os.path.join(self.tmp, "token")
        with open(token, "w") as f:
            f.write("t0k")
        env = dict(self.env, BOARD=board.url, TOKFILE=token, STATE=self.tmp, HANDLE=ME,
                   WATCH="wordsnap", FLEET_CHANNELS="lobby,findings,help,heads-up,runs", POOL="",
                   GOAL="", BACKLOG_BEFORE="4", BACKLOG_CUTOFF=str(cutoff.timestamp()))
        try:
            result = subprocess.run(["bash", "-euc", function + "\nfetch_backlog"], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([line.split("\t")[0] for line in result.stdout.splitlines()], ["3", "2"])
            with open(os.path.join(self.tmp, "backlog.next")) as f:
                self.assertEqual(f.read(), "0")
            board.page_size = 0
            result = subprocess.run(["bash", "-euc", function + "\nfetch_backlog"], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("startup catch-up pagination did not advance", result.stderr)
        finally:
            board.close()

    def test_backlog_considers_recent_replies_once_and_skips_completed_threads(self):
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=8)).isoformat()
        board = FakeBoard([
            msg(1, "wordsnap", "old request", created_at=old),
            msg(2, "wordsnap", "recent followup on older thread", thread_id=1, reply_to=1),
            msg(3, "wordsnap", "resolved request"),
            msg(4, "wordsnap", "completed reply", thread_id=3, reply_to=3, meta={"done": True}),
            msg(5, "wordsnap", "thread original request"),
            msg(6, "wordsnap", "latest discussion", thread_id=5, reply_to=5),
            msg(7, "wordsnap", "live control"),
        ], {}, cursor=6, page_size=2)
        try:
            runs = self.run_bot(board, lambda rs: any("live control" in r["prompt"] for r in rs))
        finally:
            board.close()
        prompts = "\n".join(r["prompt"] for r in runs)
        self.assertIn("[backlog: message 2, board wordsnap, thread 1", prompts)
        self.assertEqual(prompts.count("latest discussion"), 1)
        for text in ("old request", "resolved request", "completed reply", "thread original request"):
            self.assertNotIn(text, prompts)

    def test_workers_get_followups_helpers_only_get_mentions_and_losers_stay_asleep(self):
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        board = FakeBoard([
            msg(1, "wordsnap", "another bot owns this"),
            msg(2, "wordsnap", "unmentioned discussion", thread_id=1, reply_to=1),
            msg(3, "wordsnap", "@tst help with mapping", thread_id=1, reply_to=1, mention=True),
            msg(4, "wordsnap", "our untagged followup", thread_id=99, reply_to=99),
            msg(5, "wordsnap", "new open request"),
        ], {}, workers={1: "someone", 99: ME})
        try:
            runs = self.run_bot(board, lambda rs: any("new open request" in r["prompt"] for r in rs))
        finally:
            board.close()
        prompts = "\n".join(r["prompt"] for r in runs)
        for text in ("help with mapping", "our untagged followup", "new open request"):
            self.assertIn(text, prompts)
        for text in ("another bot owns this", "unmentioned discussion"):
            self.assertNotIn(text, prompts)
        self.assertEqual(board.workers[1], "someone")
        self.assertEqual(board.workers[5], ME)
        self.assertNotIn("pausing", self.log)

    def test_failed_atomic_claim_is_held_until_it_succeeds(self):
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        board = FakeBoard([msg(1, "wordsnap", "retry the pickup")], {}, claim_failures=1)
        try:
            runs = self.run_bot(board, lambda rs: any("retry the pickup" in r["prompt"] for r in rs))
        finally:
            board.close()
        self.assertIn("claim failed: HTTP 503; holding message 1", self.log)
        self.assertEqual(sum("retry the pickup" in r["prompt"] for r in runs), 1)
        self.assertEqual(board.requests.count("POST /api/threads/1/worker"), 2)

    def test_pickup_catches_replies_that_arrived_before_the_claim(self):
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        board = FakeBoard([
            msg(1, "wordsnap", "build the script"),
            msg(2, "wordsnap", "here is the schema", thread_id=1, reply_to=1),
            msg(3, "wordsnap", "also add resume", thread_id=1, reply_to=1),
        ], {}, page_size=1)
        try:
            runs = self.run_bot(board, lambda rs: any("also add resume" in r["prompt"] for r in rs))
        finally:
            board.close()
        prompts = "\n".join(r["prompt"] for r in runs)
        for text in ("build the script", "here is the schema", "also add resume"):
            self.assertEqual(prompts.count(text), 1)
        self.assertTrue(any("thread 1" in r["prompt"] and "also add resume" in r["prompt"] for r in runs))

    def test_reply_arriving_after_pickup_is_delivered_once_by_live_polling(self):
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        board = FakeBoard([msg(1, "wordsnap", "new task")], {})
        board.on_claim = lambda: board.messages.append(
            msg(2, "wordsnap", "reply during pickup", thread_id=1, reply_to=1))
        try:
            runs = self.run_bot(board, lambda rs: any("reply during pickup" in r["prompt"] for r in rs))
        finally:
            board.close()
        self.assertEqual(sum(r["prompt"].count("reply during pickup") for r in runs), 1)
        self.assertTrue(any("since=1" in path for path in board.requests if "/api/messages?" in path))

    def test_two_competing_launchers_wake_only_one_model_for_the_thread(self):
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        board = FakeBoard([msg(1, "wordsnap", "one atomic pickup")], {})
        processes = [subprocess.Popen(
            [BOT, "claude", "--pool", self.pool, "--name", handle],
            env=dict(self.env, BOARD=board.url), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        ) for handle in ("first", "second")]
        runs = []
        try:
            end = time.time() + 20
            while time.time() < end:
                time.sleep(0.2)
                if os.path.exists(self.runs):
                    with open(self.runs) as f:
                        runs = [json.loads(line) for line in f]
                if board.requests.count("POST /api/threads/1/worker") >= 2 and any(
                        "one atomic pickup" in r["prompt"] for r in runs):
                    break
        finally:
            for process in processes:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(10)
            board.close()
        self.assertGreaterEqual(board.requests.count("POST /api/threads/1/worker"), 2)
        self.assertEqual(sum("one atomic pickup" in r["prompt"] for r in runs), 1)
        self.assertIn(board.workers[1], ("first", "second"))

    def test_release_event_reopens_pickup_without_subscribing_to_other_replies(self):
        os.makedirs(os.path.join(self.pool, "wordsnap"))
        board = FakeBoard([
            msg(1, "wordsnap", "ordinary reply stays quiet", thread_id=99, reply_to=99),
            msg(2, "wordsnap", "worker released", thread_id=99, reply_to=99,
                meta={"kind": "thread_released"}),
        ], {})
        try:
            runs = self.run_bot(board, lambda rs: any("worker released" in r["prompt"] for r in rs))
        finally:
            board.close()
        prompts = "\n".join(r["prompt"] for r in runs)
        self.assertIn("worker released", prompts)
        self.assertNotIn("ordinary reply stays quiet", prompts)
        self.assertEqual(board.workers[99], ME)


if __name__ == "__main__":
    unittest.main(verbosity=2)
