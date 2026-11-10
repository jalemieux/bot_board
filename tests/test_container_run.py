"""bin/bot's container runner (#306/#477). With BOT_RUNTIME set, every harness turn and the
run-in-container subcommand go through the runtime, with the checkout bind-mounted at /work
and the state volume at /home/bot. These tests stand a fake runtime in for docker (RUNTIME,
below): it records its argv, then runs the wrapped command with cwd set to the /work source,
so the dispatch, the empty-checkout guard and the (fake) harness all behave as in a container.
Nothing here needs docker. Run with python -m unittest discover -s tests."""
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from fakes import harness as fh                  # noqa: E402
from fakes.board import FakeBoard                # noqa: E402
from test_launcher_e2e import Launcher, BOT      # noqa: E402

# A stand-in for docker/the box runtime. It records its argv (so a test can check the mounts
# and the image), then "runs the container": it finds the -v SRC:/work bind-mount and the
# command after the image, rewrites the empty-checkout guard's literal /work to SRC, and runs
# the command with cwd=SRC. So an empty checkout still trips the guard (exit 97) and a
# non-empty one runs the real (fake) harness, exactly as a container would.
RUNTIME = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
argv = sys.argv[1:]
log = os.environ.get("FAKE_RUNTIME_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps(argv) + "\n")
if not argv or argv[0] != "run":
    sys.exit(0)   # e.g. `rm -f <name>` from bot kill: recorded above, nothing to simulate
takes_value = {"-e", "--user", "-w", "-v", "--env-file", "--name", "--network", "--add-host"}
i, mounts, workdir, image, cmd = 1, [], None, None, None
while i < len(argv):
    a = argv[i]
    if a == "-v":
        src, _, dest = argv[i + 1].partition(":")
        dest = dest.split(":")[0]
        mounts.append((src, dest)); i += 2; continue
    if a == "-w":
        workdir = argv[i + 1]; i += 2; continue
    if a in takes_value:
        i += 2; continue
    if a.startswith("-"):
        i += 1; continue
    image = a; cmd = argv[i + 1:]; break
if image is None:
    sys.stderr.write("fake runtime: no image in %r\n" % (argv,)); sys.exit(2)
work_src = next((s for s, d in mounts if d == "/work"), None)
if os.environ.get("FAKE_RUNTIME_NO_MOUNT"):    # a failed bind-mount: the container sees an empty /work
    work_src = os.environ["FAKE_RUNTIME_NO_MOUNT"]
cwd = work_src if workdir == "/work" and work_src else None
# The bot's skills (#581) are mounted at /opt/bot-skills/<name>. FAKE_RUNTIME_SKILLS is a directory that
# stands for /opt/bot-skills (each mount becomes a link in it to its source), and FAKE_RUNTIME_HOME is the
# container's $HOME, so the command that links the skills runs for real.
skills_root = os.environ.get("FAKE_RUNTIME_SKILLS")
env = dict(os.environ)
if os.environ.get("FAKE_RUNTIME_HOME"):
    env["HOME"] = os.environ["FAKE_RUNTIME_HOME"]
if skills_root:
    os.makedirs(skills_root, exist_ok=True)
    for src, dest in mounts:
        if dest.startswith("/opt/bot-skills/"):
            link = os.path.join(skills_root, os.path.basename(dest))
            if not os.path.lexists(link):
                os.symlink(src, link)
# bot mode wraps the command as: sh -c '<guard>' sh <harness> <args>
if len(cmd) >= 4 and cmd[0] == "sh" and cmd[1] == "-c" and cmd[3] == "sh":
    guard = cmd[2].replace("/work", work_src or "/work")
    if skills_root:
        guard = guard.replace("/opt/bot-skills", skills_root)
    cmd = ["sh", "-c", guard, "sh"] + cmd[4:]
sys.exit(subprocess.run(cmd, cwd=cwd, env=env).returncode)
'''

HELLO = "---\nname: hello\ndescription: Says hello.\n---\n\nOne.\n"
REVIEW = "---\nname: pr-review\ndescription: Reviews.\n---\n\nTwo.\n"
SKILL = lambda name, mandatory=False: {"name": name, "mandatory": mandatory, "description": "d", "revision": 1}

RESUME_FLAG = {"claude": "--resume", "copilot": "--resume", "codex": "resume", "opencode": "--session"}


class ContainerRun(unittest.TestCase):
    def setUp(self):
        self.rt = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.rt, ignore_errors=True)
        self.runtime = Path(self.rt) / "fakeruntime"
        self.runtime.write_text(RUNTIME)
        self.runtime.chmod(self.runtime.stat().st_mode | stat.S_IEXEC)
        self.log = Path(self.rt) / "runtime.log"
        self.log.write_text("")

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines() if l.strip()]

    # ---- every harness turn goes through the runtime, with resume intact ------------------
    def test_each_harness_runs_in_the_container_and_resumes(self):
        for harness in ("claude", "codex", "opencode", "copilot"):
            with self.subTest(harness=harness), FakeBoard() as board:
                self.log.write_text("")
                # the fake board is loopback; the fake runtime shares the host network, so tell
                # the guard loopback is reachable here (BOT_CONTAINER_HOST_NETWORK)
                L = Launcher(board, harness, env={"BOT_RUNTIME": str(self.runtime),
                                                  "FAKE_RUNTIME_LOG": str(self.log),
                                                  "BOT_CONTAINER_HOST_NETWORK": "1"})
                self.addCleanup(L.close)
                # a real checkout has files; seed one so the empty-/work guard passes
                checkout = Path(L.tmp.name) / "work" / L.name
                checkout.mkdir(parents=True, exist_ok=True)
                (checkout / ".keep").write_text("a non-empty checkout")
                L.plan(fh.step(harness, "ok", "rules read"),
                       fh.step(harness, "ok", "done"),
                       fh.step(harness, "ok", "again"))
                L.start()
                L.wait_for(lambda: len(L.prompts()) >= 1, "the first turn")
                root = board.post_root("ws-general", "please fix the widget")
                L.wait_for(lambda: len(L.prompts()) >= 2, "the work turn")
                L.wait_for(lambda: L.read("session"), "a session id kept")
                sid = L.read("session")
                board.reply(root, "and the other thing too")
                L.wait_for(lambda: len(L.prompts()) >= 3, "the resume turn")
                L.wait_for(lambda: not (L.state / "busy").exists() and L.read("runs") == "3",
                           "all three runs done")

                runtime_runs = [c for c in self.calls() if c and c[0] == "run"]
                self.assertTrue(runtime_runs, "the runtime ran a container for %s" % harness)
                img = "bot_board-harness-%s:latest" % harness
                self.assertTrue(all(any(img in a for a in c) for c in runtime_runs),
                                "every run used the %s image: %s" % (harness, runtime_runs))
                self.assertEqual(L.read("health"), "", "every turn completed in the container")
                self.assertIn("please fix the widget", "\n".join(L.prompts()),
                              "the prompt reached the harness through the container")
                flag = RESUME_FLAG[harness]
                resumed = [r for r in L.runs() if flag in r and r[r.index(flag) + 1] == sid]
                self.assertTrue(resumed, "a %s resume turn carried the kept session id %s: %s"
                                % (harness, sid, L.runs()))

    # ---- the bot's skills reach the container (#581) -------------------------------------------------
    def _skills_bot(self, board, skills, chome, **extra):
        """A launcher whose bot lists `skills`, with a fake runtime that simulates the skill mounts and
        a container home at `chome`. Returns it after its first turn."""
        L = Launcher(board, "claude", env={"BOT_RUNTIME": str(self.runtime), "FAKE_RUNTIME_LOG": str(self.log),
                                           "BOT_CONTAINER_HOST_NETWORK": "1", "FAKE_RUNTIME_HOME": str(chome),
                                           "FAKE_RUNTIME_SKILLS": str(Path(self.rt) / "optskills"), **extra})
        self.addCleanup(L.close)
        checkout = Path(L.tmp.name) / "work" / L.name
        checkout.mkdir(parents=True, exist_ok=True)
        (checkout / ".keep").write_text("a non-empty checkout")
        board.profile = {"profile": {"name": "dev"}, "skills": skills}
        board.skills = {"hello": HELLO, "pr-review": REVIEW, "a:b": HELLO}
        board.skills_revision = "aaaa"
        L.plan(fh.step("claude", "ok", "rules read"))
        L.start()
        L.wait_for(lambda: len(L.prompts()) >= 1 and not (L.state / "busy").exists(), "the first turn")
        return L

    def _skill_mounts(self):
        out = {}
        for c in self.calls():
            if c and c[0] == "run":
                for i, a in enumerate(c):
                    if a == "-v" and "/opt/bot-skills/" in c[i + 1]:
                        src, dest, *mode = c[i + 1].split(":")
                        out[dest.rsplit("/", 1)[1]] = (src, mode)
        return out

    def test_a_bot_gets_the_skills_it_lists_in_its_container(self):
        with FakeBoard() as board:
            chome = Path(self.rt) / "chome"
            (chome / ".claude/skills/other").mkdir(parents=True)
            (chome / ".claude/skills/other/SKILL.md").write_text("a skill that is really there")
            (chome / ".agents/skills").mkdir(parents=True)
            os.symlink(str(Path(self.rt) / "optskills" / "old"), chome / ".agents/skills/old")   # a link an earlier turn made
            L = self._skills_bot(board, [SKILL("hello", True), SKILL("pr-review")], chome)
            home = Path(L.tmp.name)
            # no stray skill: the launcher left it alone and the bot does not list it
            stray = home / ".config/bot_board/skills/stray"
            stray.mkdir(parents=True, exist_ok=True)
            mounts = self._skill_mounts()
            self.assertEqual(sorted(mounts), ["hello", "pr-review"], "one mount per listed skill: %s" % mounts)
            for name, (src, mode) in mounts.items():
                self.assertEqual(src, os.path.realpath(home / ".config/bot_board/skills" / name),
                                 "the mount is the real directory, not the host's symlink")
                self.assertEqual(mode, ["ro"], "read-only")
            for base in (".claude/skills", ".agents/skills"):
                self.assertEqual((chome / base / "hello/SKILL.md").read_text(), HELLO, base)
                self.assertEqual((chome / base / "pr-review/SKILL.md").read_text(), REVIEW, base)
            self.assertFalse(os.path.lexists(chome / ".agents/skills/old"), "a link to a skill no longer mounted is removed")
            self.assertEqual((chome / ".claude/skills/other/SKILL.md").read_text(), "a skill that is really there",
                             "a skill that is really there is left alone")
            self.assertEqual(L.read("health"), "", "the turn completed")

    def test_a_skill_with_an_unsafe_name_is_not_mounted(self):
        with FakeBoard() as board:
            self._skills_bot(board, [SKILL("hello"), SKILL("a:b")], Path(self.rt) / "chome")
            self.assertEqual(sorted(self._skill_mounts()), ["hello"])

    def test_a_bot_with_no_skills_gets_no_mounts_and_loses_stale_links(self):
        with FakeBoard() as board:
            chome = Path(self.rt) / "chome"
            (chome / ".claude/skills").mkdir(parents=True)
            os.symlink(str(Path(self.rt) / "optskills" / "old"), chome / ".claude/skills/old")
            self._skills_bot(board, [], chome)
            self.assertEqual(self._skill_mounts(), {})
            self.assertFalse(os.path.lexists(chome / ".claude/skills/old"))

    # ---- the opencode model and config cross into the container (#324 + #477) -------------
    def test_opencode_model_and_config_cross_into_the_container(self):
        with FakeBoard() as board:
            self.log.write_text("")
            L = Launcher(board, "opencode", env={"BOT_RUNTIME": str(self.runtime),
                                                 "FAKE_RUNTIME_LOG": str(self.log),
                                                 "BOT_CONTAINER_HOST_NETWORK": "1"})
            self.addCleanup(L.close)
            checkout = Path(L.tmp.name) / "work" / L.name
            checkout.mkdir(parents=True, exist_ok=True)
            (checkout / ".keep").write_text("a non-empty checkout")
            cfg = Path(L.tmp.name) / "local.json"
            cfg.write_text('{"provider": {"llama.cpp": {}}}')
            L.plan(fh.step("opencode", "ok", "rules read"), fh.step("opencode", "ok", "done"))
            L.start("--model", "llama.cpp/qwen", "--opencode-config", str(cfg))
            L.wait_for(lambda: L.read("runs") == "1", "the first turn")
            oc = [a for a in L.runs() if a[:1] == ["run"]]
            self.assertTrue(oc, "an opencode run reached the harness: %s" % L.runs())
            self.assertEqual(oc[0][oc[0].index("--model") + 1], "llama.cpp/qwen",
                             "the model crossed as an argument: %s" % oc[0])
            self.assertTrue(any("OPENCODE_CONFIG_CONTENT" in c for c in self.calls()),
                            "the runtime was told to forward the config env: %s" % self.calls())
            seen = {json.loads(l) for l in (L.scenario / "opencode.config").read_text().splitlines() if l.strip()}
            self.assertIn('{"provider": {"llama.cpp": {}}}', seen,
                          "the config content reached opencode inside the container")

    # ---- the subcommand, the way the keeper calls it -------------------------------------
    def sub_env(self, extra=None):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        bindir, scen = Path(home) / "bin", Path(home) / "scen"
        fh.install(str(bindir), str(scen))
        env = {**os.environ, "HOME": home, "BOT_CONFIG": str(Path(home) / "missing"),
               "BOT_RUNTIME": str(self.runtime), "FAKE_RUNTIME_LOG": str(self.log),
               "FAKE_SCENARIO_DIR": str(scen),
               "PATH": str(bindir) + os.pathsep + os.environ["PATH"]}
        env.update(extra or {})
        return env

    def run_sub(self, args, env):
        return subprocess.run([BOT, "run-in-container", *args], env=env,
                              capture_output=True, text=True, timeout=30)

    def test_box_mode_mounts_the_shared_volume_and_no_checkout(self):
        self.log.write_text("")
        r = self.run_sub(["--box", "claude", "--", "--version"], self.sub_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 1, calls)
        c = calls[0]
        self.assertIn("bot_board-box-state:/home/bot", c, c)
        self.assertFalse(any(a.endswith(":/work") for a in c), "box mode mounts no checkout: %s" % c)
        self.assertNotIn("BOARD", c, "box mode does not pass the board vars: %s" % c)
        self.assertFalse(any("/opt/bot-skills" in a for a in c), "box mode mounts no skills: %s" % c)

    # ---- /fleet is the only source of a bot's secrets (#555) --------------------------------------
    def _fleet_env(self, fleet=None, extra=None, pop=("GH_TOKEN", "GITHUB_TOKEN", "FORGEJO_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")):
        # a sub env with a fleet.env holding the given secrets. The named secrets are popped from the
        # launcher's own environment first, so the scenario is controlled: a secret reaches a bot only
        # when it is on /fleet (in fleet.env), never because the launcher happened to hold it.
        env = self.sub_env({"BOT_CONTAINER_HOST_NETWORK": "1", **(extra or {})})
        for k in pop:
            env.pop(k, None)
        if fleet:
            fe = Path(env["HOME"]) / ".config" / "bot_board"
            fe.mkdir(parents=True, exist_ok=True)
            (fe / "fleet.env").write_text("".join("%s=%s\n" % (k, v) for k, v in fleet.items()))
        return env

    def _checkout(self):
        c = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, c, ignore_errors=True)
        (Path(c) / ".keep").write_text("a non-empty checkout")
        return c

    def _run(self):
        return next((c for c in self.calls() if c and c[0] == "run"), None)

    def test_a_fleet_secret_is_forwarded_to_the_bot_container(self):
        # every /fleet secret rides $_FLEET_SET into the container by name, the claude login and the
        # forge token among them, so no secret needs its own forwarding line any more (#555)
        self.log.write_text("")
        env = self._fleet_env({"CLAUDE_CODE_OAUTH_TOKEN": "fleet-oauth", "FORGEJO_TOKEN": "fleet-forge"})
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", self._checkout(), "--", "--version"], env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        run = self._run()
        self.assertIsNotNone(run, self.calls())
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", run, "claude login forwarded by name: %s" % run)
        self.assertIn("FORGEJO_TOKEN", run, "forge token forwarded by name: %s" % run)
        self.assertNotIn("fleet-oauth", " ".join(run), "passed by name, not value")

    def test_a_launcher_env_only_secret_does_not_reach_the_container(self):
        # a secret the launcher's own environment has but /fleet does not (the operator's own login)
        # is not on $_FLEET_SET, so it is never forwarded (#555)
        self.log.write_text("")
        env = self._fleet_env({"FORGEJO_TOKEN": "fleet-forge"}, extra={"OPERATOR_TOKEN": "from-box"})
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", self._checkout(), "--", "--version"], env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        run = self._run()
        self.assertIsNotNone(run, self.calls())
        self.assertIn("FORGEJO_TOKEN", run, "the /fleet secret is forwarded: %s" % run)
        self.assertNotIn("OPERATOR_TOKEN", run, "a box-only secret is not: %s" % run)

    def test_a_fleet_secret_is_forwarded_in_box_mode(self):
        # the keeper's login check runs in box mode and the claude login reaches it the same way
        self.log.write_text("")
        env = self._fleet_env({"CLAUDE_CODE_OAUTH_TOKEN": "fleet-oauth"})
        r = self.run_sub(["--box", "claude", "--", "--version"], env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        run = self._run()
        self.assertIsNotNone(run, self.calls())
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", run, run)

    def test_a_fleet_gh_token_reaches_a_non_copilot_container(self):
        # GH_TOKEN and GITHUB_TOKEN ride $_FLEET_SET like any /fleet secret for a non-copilot harness
        self.log.write_text("")
        env = self._fleet_env({"GH_TOKEN": "fleet-gh", "GITHUB_TOKEN": "fleet-github"})
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", self._checkout(), "--", "--version"], env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        run = self._run()
        self.assertIsNotNone(run, self.calls())
        self.assertIn("GH_TOKEN", run, run)
        self.assertIn("GITHUB_TOKEN", run, run)
        self.assertNotIn("fleet-gh", " ".join(run), "passed by name, not value")

    def test_a_fleet_gh_token_is_still_skipped_for_copilot(self):
        # copilot refuses a gh token in its own environment without Copilot Requests permission, so
        # GH_TOKEN and GITHUB_TOKEN are skipped for it whatever the source; other /fleet secrets still
        # reach it (#542, #545)
        self.log.write_text("")
        env = self._fleet_env({"GH_TOKEN": "fleet-gh", "GITHUB_TOKEN": "fleet-github", "FORGEJO_TOKEN": "fleet-forge"})
        r = self.run_sub(["copilot", "--handle", "tbot", "--dir", self._checkout(), "--", "--version"], env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        run = self._run()
        self.assertIsNotNone(run, self.calls())
        self.assertNotIn("GH_TOKEN", run, "copilot gets no gh token: %s" % run)
        self.assertNotIn("GITHUB_TOKEN", run, run)
        self.assertIn("FORGEJO_TOKEN", run, "but still gets other /fleet secrets: %s" % run)

    def test_forge_host_is_mapped_into_the_bridge_container(self):
        # the harness reaches the forge by its tailnet name; a bridge container must resolve it too
        forge = "minipc-2.taild87368.ts.net"
        ip = self._resolve(forge)
        if ip is None or ip.startswith("127.") or ip == "::1":
            self.skipTest("the forge host does not resolve to a non-loopback address here")
        checkout = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, checkout, ignore_errors=True)
        (Path(checkout) / ".keep").write_text("a non-empty checkout")
        self.log.write_text("")
        # a bare-IP board (no mapping) so the forge host is what the run carries
        env = self.sub_env({"BOARD": "http://93.184.216.34:8080",
                            "BOT_HARNESS_REGISTRY": forge + ":3000"})
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", checkout, "--", "--version"], env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        run = next((c for c in self.calls() if c and c[0] == "run"), None)
        self.assertIsNotNone(run, self.calls())
        self.assertIn("%s:%s" % (forge, ip), run, "forge host mapped into the container: %s" % run)

    def test_unresolvable_or_loopback_extra_host_is_skipped_not_fatal(self):
        checkout = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, checkout, ignore_errors=True)
        (Path(checkout) / ".keep").write_text("a non-empty checkout")
        self.log.write_text("")
        # bare-IP board and registry so neither is mapped; the extra hosts are what we check.
        # Unlike BOARD, an extra host that will not resolve, or is loopback, is skipped, not fatal.
        env = self.sub_env({"BOARD": "http://93.184.216.34:8080",
                            "BOT_HARNESS_REGISTRY": "203.0.113.5:3000",
                            "BOT_CONTAINER_HOSTS": "no-such-host.invalid localhost"})
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", checkout, "--", "--version"], env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        run = next((c for c in self.calls() if c and c[0] == "run"), None)
        self.assertIsNotNone(run, self.calls())
        self.assertFalse(any("no-such-host.invalid" in a for a in run), "unresolvable host skipped: %s" % run)
        self.assertFalse(any(a == "--add-host" for a in run), "no mapping for bare-IP board/registry or loopback hosts: %s" % run)

    def test_a_failed_mount_of_a_checkout_with_files_fails_loud_with_exit_97(self):
        self.log.write_text("")
        host = tempfile.mkdtemp()
        unmounted = tempfile.mkdtemp()       # what the container sees instead of the host directory
        self.addCleanup(shutil.rmtree, host, ignore_errors=True)
        self.addCleanup(shutil.rmtree, unmounted, ignore_errors=True)
        (Path(host) / ".keep").write_text("files on the host")
        # opt out of the loopback-board guard so it cannot preempt the /work guard under test
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", host, "--", "--version"],
                         self.sub_env({"BOT_CONTAINER_HOST_NETWORK": "1", "FAKE_RUNTIME_NO_MOUNT": unmounted}))
        self.assertEqual(r.returncode, 97, r.stdout + r.stderr)
        self.assertIn("did not bind-mount", r.stderr)

    def test_an_empty_host_checkout_is_not_a_failed_mount_and_runs(self):
        self.log.write_text("")
        empty = tempfile.mkdtemp()           # a new bot that has cloned nothing yet
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", empty, "--", "--version"],
                         self.sub_env({"BOT_CONTAINER_HOST_NETWORK": "1"}))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_bot_mode_needs_a_checkout_dir(self):
        r = self.run_sub(["claude", "--handle", "tbot", "--", "--version"], self.sub_env())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("needs --dir", r.stderr)

    def test_loopback_board_is_refused_on_bridge(self):
        checkout = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, checkout, ignore_errors=True)
        (Path(checkout) / ".keep").write_text("a non-empty checkout")
        # a loopback board the bridge container cannot reach is refused, naming the setting
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", checkout, "--", "--version"],
                         self.sub_env({"BOARD": "http://127.0.0.1:8080"}))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cannot reach", r.stderr)

    def test_host_network_opt_out_adds_network_host(self):
        checkout = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, checkout, ignore_errors=True)
        (Path(checkout) / ".keep").write_text("a non-empty checkout")
        self.log.write_text("")
        # with the opt-out a loopback board is allowed and the run gets --network host
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", checkout, "--", "--version"],
                         self.sub_env({"BOARD": "http://127.0.0.1:8080", "BOT_CONTAINER_HOST_NETWORK": "1"}))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        run = next((c for c in self.calls() if c and c[0] == "run"), None)
        self.assertIsNotNone(run, self.calls())
        self.assertIn("--network", run, run)
        self.assertEqual(run[run.index("--network") + 1], "host", run)
        self.assertFalse(any(a == "--add-host" for a in run), "host net needs no add-host: %s" % run)
        # the kill name and the stale-leftover cleanup still hold
        name = "bot_board-tbot-run"
        self.assertIn(name, run)
        self.assertTrue(any(c[:2] == ["rm", "-f"] and name in c for c in self.calls()), self.calls())

    def _resolve(self, host):
        try:
            return socket.gethostbyname(host)
        except OSError:
            return None

    def test_hostname_board_resolving_to_a_real_ip_is_mapped_in(self):
        # the box's own name, only when it resolves to a non-loopback address (true on a Mac,
        # not on a Debian/Ubuntu box where /etc/hosts gives it 127.0.1.1 — that is the next test)
        host = socket.gethostname()
        ip = self._resolve(host)
        if ip is None or ip.startswith("127.") or ip == "::1":
            self.skipTest("this box's name does not resolve to a non-loopback address")
        checkout = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, checkout, ignore_errors=True)
        (Path(checkout) / ".keep").write_text("a non-empty checkout")
        self.log.write_text("")
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", checkout, "--", "--version"],
                         self.sub_env({"BOARD": "http://%s:8080" % host}))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        run = next((c for c in self.calls() if c and c[0] == "run"), None)
        self.assertIsNotNone(run, self.calls())
        self.assertIn("--add-host", run, "a hostname board is mapped in: %s" % run)
        self.assertEqual(run[run.index("--add-host") + 1], "%s:%s" % (host, ip),
                         "add-host maps the board name to its resolved IP")
        self.assertFalse(any(a == "--network" for a in run), "bridge, not host net: %s" % run)

    def test_hostname_board_resolving_to_loopback_is_refused(self):
        # the #504-review bug: a name that resolves to 127.x (Debian/Ubuntu map the box's own
        # name to 127.0.1.1) must be refused, not mapped in, or the turn posts nothing again
        host = socket.gethostname()
        ip = self._resolve(host)
        if ip is None or not (ip.startswith("127.") or ip == "::1"):
            self.skipTest("this box's name does not resolve to a loopback address")
        checkout = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, checkout, ignore_errors=True)
        (Path(checkout) / ".keep").write_text("a non-empty checkout")
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", checkout, "--", "--version"],
                         self.sub_env({"BOARD": "http://%s:8080" % host}))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("loopback", r.stderr)

    def test_unresolvable_board_fails_loud(self):
        checkout = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, checkout, ignore_errors=True)
        (Path(checkout) / ".keep").write_text("a non-empty checkout")
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", checkout, "--", "--version"],
                         self.sub_env({"BOARD": "http://no-such-host.invalid:8080"}))
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cannot resolve", r.stderr)

    def test_bare_ip_board_needs_no_mapping(self):
        checkout = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, checkout, ignore_errors=True)
        (Path(checkout) / ".keep").write_text("a non-empty checkout")
        self.log.write_text("")
        # a bare-IP registry too, so the forge host adds no mapping and the board is what we check
        r = self.run_sub(["claude", "--handle", "tbot", "--dir", checkout, "--", "--version"],
                         self.sub_env({"BOARD": "http://93.184.216.34:8080",
                                       "BOT_HARNESS_REGISTRY": "203.0.113.5:3000"}))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        run = next((c for c in self.calls() if c and c[0] == "run"), None)
        self.assertIsNotNone(run, self.calls())
        self.assertFalse(any(a in ("--add-host", "--network") for a in run),
                         "a bare IP board and registry need neither mapping nor host net: %s" % run)

    def test_kill_removes_the_named_container(self):
        with FakeBoard() as board:
            self.log.write_text("")
            L = Launcher(board, "claude", env={"BOT_RUNTIME": str(self.runtime),
                                               "FAKE_RUNTIME_LOG": str(self.log),
                                               "BOT_CONTAINER_HOST_NETWORK": "1"})
            self.addCleanup(L.close)
            checkout = Path(L.tmp.name) / "work" / L.name
            checkout.mkdir(parents=True, exist_ok=True)
            (checkout / ".keep").write_text("a non-empty checkout")
            L.plan(fh.step("claude", "ok", "rules read"))
            L.start()
            L.wait_for(lambda: len(L.prompts()) >= 1, "the first turn")
            r = subprocess.run([BOT, "kill", L.name], env=L.env, capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            name = "bot_board-%s-run" % L.name
            self.assertTrue(any(c[:2] == ["rm", "-f"] and name in c for c in self.calls()),
                            "bot kill removed the named container: %s" % self.calls())
            self.assertTrue(any(call and call[0] == "run" and name in call for call in self.calls()),
                            "the turn ran in the named container: %s" % self.calls())


if __name__ == "__main__":
    unittest.main()
