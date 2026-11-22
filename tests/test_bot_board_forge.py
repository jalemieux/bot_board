"""bot_board install --forge: the board and Forgejo in Docker, wired together, and a first bot in a container, in one
line. Docker, curl, jq and the keeper installer are fakes that log what they were asked; nothing here touches a real
Docker. Run with python -m unittest discover -s tests."""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FAKE_JQ = r'''#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
if a[:1] == ["-n"]:                      # jq -n --arg k v '<filter>'
    args = {a[i + 1]: a[i + 2] for i in range(len(a)) if a[i] == "--arg"}
    f = a[-1]
    if "kind" in f:
        print(json.dumps({"handle": args["h"], "kind": "human", "invite_code": args["i"]}))
    else:
        print(json.dumps({"value": args["v"]}))
else:                                     # jq -r .token
    print(json.load(sys.stdin)["token"])
'''
FAKE_CURL = r'''#!/usr/bin/env python3
import os, sys
log = os.environ["FAKE_LOG"]
open(log, "a").write("curl " + " ".join(sys.argv[1:]) + "\n")
if "-d" in sys.argv:
    open(log, "a").write("body " + sys.argv[sys.argv.index("-d") + 1] + "\n")
url = [x for x in sys.argv[1:] if x.startswith("http")][0]
if url.endswith("/api/agents"):
    print('{"token": "tok-human"}')
'''
FAKE_DOCKER = r'''#!/bin/sh
echo "docker $*" >> "$FAKE_LOG"
exit 0
'''
FAKE_BOT = r'''#!/bin/sh
echo "bot $* BOARD=$BOARD" >> "$FAKE_LOG"
'''
FAKE_RUN = r'''#!/bin/sh
echo "run.sh $*" >> "$FAKE_LOG"
'''
FAKE_NATIVE = r'''#!/bin/sh
echo "forgejo-native $1 BOARD=$BOARD" >> "$FAKE_LOG"
[ "$1" = env ] && echo "export FORGE=http://127.0.0.1:3001 FORGEJO_BIN=/x/forgejo FORGEJO_WORK_PATH=/x/work"
[ "$1" = status ] && echo "forgejo-native: running at http://localhost:3001/"
exit 0
'''
FAKE_SETUP = r'''#!/bin/sh
echo "forge-setup BOARD=$BOARD TOKEN=$BOARD_HUMAN_TOKEN FORGE=$FORGE" >> "$FAKE_LOG"
'''


def script(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class ForgeInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.src = home / ".bot_board" / "src"
        (self.src / "bin").mkdir(parents=True)
        (self.src / "deploy").mkdir()
        shutil.copy(ROOT / "bin" / "bot_board", self.src / "bin" / "bot_board")
        script(self.src / "bin" / "bot", FAKE_BOT)
        script(self.src / "deploy" / "forge-setup", FAKE_SETUP)
        (self.src / ".env.example").write_text("# example\nBOT_BOARD_BIND_IP=127.0.0.1\n#FORGEJO_HOST=board-box.example.ts.net\n")
        (self.src / "deploy" / "harness.Dockerfile").write_text("FROM scratch\n")
        self.bin = home / "fakebin"
        self.bin.mkdir()
        for name, text in (("jq", FAKE_JQ), ("curl", FAKE_CURL), ("docker", FAKE_DOCKER)):
            script(self.bin / name, text)
        self.log = home / "log"
        self.log.write_text("")
        self.cfg = home / ".config" / "bot_board" / "config"
        self.env = {**os.environ, "HOME": str(home), "PATH": str(self.bin) + os.pathsep + os.environ["PATH"], "FAKE_LOG": str(self.log),
                    "BOT_BOARD_SRC": str(self.src), "BOT_BOARD_DIR": str(home / ".bot_board" / "board"), "BOT_BOARD_FROM_SRC": "1",
                    "BOT_CONFIG": str(self.cfg), "HANDLE": "ada"}
        for k in ("BOT_BOARD_BIND_IP", "FORGEJO_HOST", "CLAUDE_CODE_OAUTH_TOKEN", "HARNESS", "DOCKER", "BOARD"):
            self.env.pop(k, None)

    def run_install(self, *extra, **env):
        return subprocess.run(["bash", str(self.src / "bin" / "bot_board"), "install", "--forge", *extra],
                              env={**self.env, **env}, capture_output=True, text=True, timeout=60)

    def calls(self):
        return self.log.read_text().splitlines()

    def started(self):
        return [c for c in self.calls() if c != "docker compose version"]      # the preflight check starts nothing

    def test_one_line_brings_up_the_board_forgejo_the_wiring_and_a_first_bot_in_a_container(self):
        r = self.run_install(CLAUDE_CODE_OAUTH_TOKEN="sk-test", FORGEJO_HOST="box.example", BOT_BOARD_BIND_IP="100.64.0.9")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = "\n".join(self.calls())
        self.assertIn("docker compose --profile forge up -d --build", calls)
        self.assertIn("docker build --build-arg HARNESS=claude", calls)
        self.assertIn("-t localhost/jac/bot_board-harness-claude:latest", calls)
        self.assertIn("forge-setup BOARD=http://127.0.0.1:8080 TOKEN=tok-human FORGE=http://127.0.0.1:3000", calls)
        self.assertIn("/api/fleet/secrets/CLAUDE_CODE_OAUTH_TOKEN", calls, "the claude token goes to the board as a fleet secret")
        self.assertIn("bot install --no-bot BOARD=http://127.0.0.1:8080", calls)
        self.assertLess(calls.index("docker compose"), calls.index("forge-setup"), "Forgejo is up before it is wired")
        self.assertLess(calls.index("forge-setup"), calls.index("bot install"), "and the keeper comes last")
        config = self.cfg.read_text()
        for line in ('BOT_RUNTIME="docker"', 'BOT_CONTAINER_HOST_NETWORK="1"', 'BOT_HARNESS_REGISTRY="localhost"', 'KEEPER_DEFAULT_BOT="claude"'):
            self.assertIn(line, config)
        env = (self.src / ".env").read_text()
        self.assertIn("BOT_BOARD_BIND_IP=100.64.0.9", env)
        self.assertIn("FORGEJO_HOST=box.example", env)
        self.assertNotIn("127.0.0.1", env, "the old bind address is replaced, not kept beside the new one")

    def native(self, **env):
        script(self.src / "run.sh", FAKE_RUN)
        script(self.src / "deploy" / "forgejo-native", FAKE_NATIVE)
        script(self.src / "deploy" / "forge-setup", "#!/bin/sh\necho \"forge-setup BOARD=$BOARD TOKEN=$BOARD_HUMAN_TOKEN FORGE=$FORGE BIN=$FORGEJO_BIN\" >> \"$FAKE_LOG\"\n")
        return self.run_install(**{"FORGE_NATIVE": "1", **env})

    def test_without_docker_the_board_and_a_native_forgejo_are_wired_and_the_bots_run_as_you(self):
        r = self.native()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = "\n".join(self.calls())
        self.assertNotIn("docker", calls)
        self.assertIn("forge-setup BOARD=http://127.0.0.1:8080 TOKEN=tok-human FORGE=http://127.0.0.1:3001 BIN=/x/forgejo", calls)
        self.assertLess(calls.index("run.sh up"), calls.index("forgejo-native start"), "the board first")
        self.assertLess(calls.index("forgejo-native start"), calls.index("forge-setup"), "Forgejo is up before it is wired")
        self.assertLess(calls.index("forge-setup"), calls.index("bot install"), "and the keeper comes last")
        config = self.cfg.read_text()
        self.assertIn('KEEPER_DEFAULT_BOT="claude"', config)
        self.assertNotIn("BOT_RUNTIME", config, "no container runtime: the bots run as the user")
        self.assertIn("running as you", r.stdout)
        self.assertIn("admin password", r.stdout)

    def test_a_docker_command_with_a_prefix_stays_on_docker(self):
        r = self.run_install(DOCKER="env docker")      # as "sudo docker" would be
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("docker compose --profile forge up -d --build", "\n".join(self.calls()))

    def test_no_docker_on_the_path_is_enough_to_go_native(self):
        script(self.src / "run.sh", FAKE_RUN)
        script(self.src / "deploy" / "forgejo-native", FAKE_NATIVE)
        (self.bin / "docker").unlink()
        r = self.run_install(PATH=str(self.bin) + os.pathsep + "/usr/bin:/bin")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("forgejo-native start", "\n".join(self.calls()))

    def test_the_invite_code_goes_with_the_sign_in(self):
        r = self.run_install(INVITE="open-sesame")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('"invite_code": "open-sesame"', "\n".join(self.calls()))

    def test_running_it_again_does_not_double_the_config(self):
        self.run_install()
        self.run_install()
        config = self.cfg.read_text()
        self.assertEqual(config.count("BOT_RUNTIME="), 1)
        self.assertEqual((self.src / ".env").read_text().count("BOT_BOARD_BIND_IP="), 1)

    def test_another_harness_is_built_and_chosen(self):
        r = self.run_install(HARNESS="codex")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("HARNESS=codex", "\n".join(self.calls()))
        self.assertIn('KEEPER_DEFAULT_BOT="codex"', self.cfg.read_text())
        self.assertNotIn("fleet/secrets", "\n".join(self.calls()), "no claude token, none sent")

    def test_it_stops_before_starting_anything_when_it_cannot_work(self):
        uname = self.bin / "uname"
        script(uname, "#!/bin/sh\necho Darwin\n")
        r = self.run_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("only Linux has", r.stderr)
        self.assertEqual(self.started(), [], "a Mac: nothing was started")
        uname.unlink()
        r = self.run_install(HANDLE="a")                                  # not a handle
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("HANDLE", r.stderr)
        r = self.run_install(HARNESS="vim")
        self.assertIn("HARNESS", r.stderr)
        self.assertEqual(self.started(), [])
        r = self.run_install(FORGE_NATIVE="1", HANDLE="a")
        self.assertIn("HANDLE", r.stderr)

    def test_forge_goes_only_with_install_and_with_the_whole_set(self):
        r = subprocess.run(["bash", str(self.src / "bin" / "bot_board"), "status", "--forge"], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertIn("--forge goes with install", r.stderr)
        r = self.run_install("board")
        self.assertIn("together", r.stderr)


if __name__ == "__main__":
    unittest.main()
