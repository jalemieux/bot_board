import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bin/bot").read_text()
WAIT = SOURCE[SOURCE.index("wait_or_stop() {"):SOURCE.index("fetch_new() {")]


class InstallTests(unittest.TestCase):
    def test_piped_install_consumes_download_before_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            checkout = home / "checkout/src"
            (checkout / ".git").mkdir(parents=True)
            (checkout / "bin").mkdir()
            bot = checkout / "bin/bot"
            bot.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
            bot.chmod(0o755)
            commands = home / "commands"
            commands.mkdir()
            git = commands / "git"
            git.write_text("#!/usr/bin/env bash\nexit 0\n")
            git.chmod(0o755)
            download = home / "download"
            download.write_text(SOURCE + "\n#" + " " * 1024 * 1024 + "\n")
            config = home / "config"
            env = {
                **os.environ, "HOME": tmp, "BOT_CONFIG": str(config),
                "BOT_BOARD_DIR": str(home / "checkout"),
                "BOARD": "http://board.invalid:8080",
                "KEEPER_HOST": "box-1", "BOT_INVITE": "invite",
                "PATH": str(commands) + os.pathsep + os.environ["PATH"],
            }
            with subprocess.Popen(
                ["cat", str(download)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ) as producer:
                try:
                    result = subprocess.run(
                        ["bash", "-s", "--", "install", "--example"],
                        stdin=producer.stdout, env=env, capture_output=True,
                        text=True, timeout=10,
                    )
                finally:
                    producer.stdout.close()
                    _, error = producer.communicate(timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), ["keeper", "install", "--example"])
                self.assertEqual(producer.returncode, 0, error.decode())
            self.assertIn('BOARD="http://board.invalid:8080"', config.read_text())
            self.assertIn('KEEPER_HOST="box-1"', config.read_text())
            self.assertIn('BOT_INVITE="invite"', config.read_text())


class CopilotLauncherTests(unittest.TestCase):
    def test_permissions_on_first_and_resumed_turns(self):
        source = (ROOT / "bin/bot").read_text()
        default = next(
            line for line in source.splitlines()
            if line.startswith("BOT_COPILOT_FLAGS=")
        )
        runner = source.split("run_harness() {", 1)[1].split(
            "\n# ---- where a message", 1
        )[0]
        script = default + "\nrun_harness() {" + runner + """
HARNESS=copilot
STATE=$TEST_STATE
LOG=$STATE/log
load_fleet_env() { :; }
said_limited() { :; }
copilot() { printf '%s\\n' "$@" >> "$STATE/args"; }
run_harness "$STATE" "$STATE/session" "first turn"
run_harness "$STATE" "$STATE/session" "resumed turn"
"""
        for override in (None, "--allow-all-tools --add-dir /tokens --allow-url localhost"):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as tmp:
                env = {**os.environ, "TEST_STATE": tmp}
                env.pop("BOT_COPILOT_FLAGS", None)
                if override is not None:
                    env["BOT_COPILOT_FLAGS"] = override
                subprocess.run(
                    ["bash", "-euc", script], env=env, check=True,
                    capture_output=True, text=True,
                )
                state = Path(tmp)
                sid = (state / "session").read_text().strip()
                flags = (override or "--allow-all").split()
                self.assertEqual(
                    (state / "args").read_text().splitlines(),
                    [
                        "-s", "--session-id", sid, *flags, "-p", "first turn",
                        "-s", "--resume", sid, *flags, "-p", "resumed turn",
                    ],
                )
                self.assertEqual((state / "runs").read_text().strip(), "2")


class StopTests(unittest.TestCase):
    def test_retry_delay_is_preserved_unless_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = WAIT + """
STATE=$TEST_STATE
sleep() { echo "$1" >> "$STATE/sleeps"; }
wait_or_stop 5
touch "$STATE/stop"
wait_or_stop 60
"""
            subprocess.run(
                ["bash", "-euc", script], env={**os.environ, "TEST_STATE": tmp},
                check=True, capture_output=True, text=True,
            )
            self.assertEqual((Path(tmp) / "sleeps").read_text().splitlines(), ["1"] * 5)

    def test_stop_interrupts_idle_retry_paths(self):
        loop = SOURCE[SOURCE.index('trap \'log "stopping (seen='):
                      SOURCE.index("  RAW=$NEW;")] + "\ndone\n"
        fetch = SOURCE[SOURCE.index("fetch_new() {"):SOURCE.index("# ---- health:")]
        for reason in ("logged_out", "usage_limit", "backlog_failure", "fetch_failure", "long_poll"):
            with self.subTest(reason=reason):
                self.check_stop(WAIT + fetch + """
SEEN=0
HARNESS=claude
OUTAGE=""
OUTAGE_BY_OUTPUT=""
BACKLOG_BEFORE=0
BOT_WAIT=60
BOARD=http://unused.invalid
TOKFILE=$STATE/token
touch "$TOKFILE" "$STATE/health"
echo "$SEEN" > "$STATE/seen"
rotate_logs() { :; }
log() { echo "$*"; }
logged_out() { [ "$REASON" = logged_out ]; }
fetch_backlog() { return 1; }
curl() {
  if [ "$REASON" = long_poll ]; then
    touch "$STATE/waiting"
    exec sleep 60
  fi
  return 1
}
sleep() { touch "$STATE/waiting"; command sleep "$@"; }
case $REASON in
  logged_out) OUTAGE=1 ;;
  usage_limit) OUTAGE=1; OUTAGE_BY_OUTPUT=1 ;;
  backlog_failure) BACKLOG_BEFORE=2 ;;
esac
""" + loop, reason)

    def test_stop_allows_busy_run_to_finish(self):
        self.check_stop("""
echo project > "$STATE/busy"
touch "$STATE/waiting"
sleep 1
touch "$STATE/completed"
rm "$STATE/busy"
while [ ! -e "$STATE/stop" ]; do sleep 1; done
""", "busy")

    def check_stop(self, body, reason):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state = home / ".config/bot_board/bots/test"
            state.mkdir(parents=True)
            script = home / "bot"
            script.write_text("""set -euo pipefail
STATE=$TEST_STATE
echo $$ > "$STATE/pid"
trap 'rm -f "$STATE/pid"' EXIT
""" + body)
            env = {**os.environ, "HOME": tmp, "BOT_CONFIG": str(home / "missing"),
                   "TEST_STATE": str(state), "REASON": reason}
            with subprocess.Popen(
                ["bash", str(script)], env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
            ) as launcher:
                try:
                    deadline = time.monotonic() + 5
                    while (not (state / "waiting").exists()
                           or (reason == "long_poll" and not (state / "poll").exists())):
                        if launcher.poll() is not None:
                            self.fail(launcher.communicate())
                        self.assertLess(time.monotonic(), deadline, "launcher did not start waiting")
                        time.sleep(0.01)
                    stopped = subprocess.run(
                        ["bash", str(ROOT / "bin/bot"), "stop", "test"],
                        env=env, capture_output=True, text=True, timeout=6,
                    )
                    self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
                    self.assertIn("test stopped", stopped.stdout)
                    launcher.communicate(timeout=2)
                    self.assertEqual(launcher.returncode, 0)
                    if reason == "busy":
                        self.assertTrue((state / "completed").exists())
                        self.assertIn("will stop when its run", stopped.stdout)
                finally:
                    if launcher.poll() is None:
                        launcher.kill()
                        launcher.communicate()


if __name__ == "__main__":
    unittest.main()

class AuthAndCapacityTests(unittest.TestCase):
    def test_auth_requires_explicit_logout(self):
        auth = SOURCE[SOURCE.index('logged_out() {'):SOURCE.index('\n# this run', SOURCE.index('logged_out() {'))]
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'codex'
            for output, code, expected in [('Logged in using ChatGPT', 0, 1),
                                            ('Not logged in', 1, 0),
                                            ('service unavailable', 1, 1)]:
                with self.subTest(output=output):
                    fake.write_text(f'#!/bin/sh\necho "{output}" >&2\nexit {code}\n')
                    fake.chmod(0o755)
                    result = subprocess.run(['bash', '-c', auth + '\nHARNESS=codex; logged_out'],
                                            env={**os.environ, 'PATH': tmp + ':' + os.environ['PATH']})
                    self.assertEqual(result.returncode, expected)
            fake.unlink()
            (Path(tmp) / 'python3').symlink_to(__import__('sys').executable)
            result = subprocess.run(['/bin/bash', '-c', auth + '\nHARNESS=codex; logged_out'], env={**os.environ, 'PATH': tmp})
            self.assertEqual(result.returncode, 1)


class RunShTests(unittest.TestCase):
    """#159: run.sh keeps its board apart from bot install's ~/.bot_board/src."""

    def run_sh(self, home, *args):
        env = {k: v for k, v in os.environ.items() if k != "BOT_BOARD_DIR"}
        env["HOME"] = home
        return subprocess.run(["bash", str(ROOT / "run.sh"), *args], env=env,
                              capture_output=True, text=True, timeout=30)

    def test_old_board_is_stopped_and_moved_to_its_own_dir(self):
        with tempfile.TemporaryDirectory() as home:
            old = Path(home, ".bot_board")
            (old / "src").mkdir(parents=True)
            (old / "board.db").write_text("db")
            (old / "board.log").write_text("log")
            board = subprocess.Popen(["sleep", "60"])
            try:
                (old / "board.pid").write_text(str(board.pid))
                result = self.run_sh(home, "stop")
                self.assertEqual(result.returncode, 0, result.stderr)
                board.wait(timeout=10)
            finally:
                board.kill()
            new = old / "board"
            self.assertEqual((new / "board.db").read_text(), "db")
            self.assertEqual((new / "board.log").read_text(), "log")
            self.assertFalse((old / "board.db").exists())
            self.assertFalse((old / "board.pid").exists())
            self.assertTrue((old / "src").is_dir())     # bot install's checkout is left alone

    def test_new_layout_is_left_alone(self):
        with tempfile.TemporaryDirectory() as home:
            new = Path(home, ".bot_board/board")
            new.mkdir(parents=True)
            (new / "board.db").write_text("current")
            Path(home, ".bot_board/board.db").write_text("stale")
            result = self.run_sh(home, "stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((new / "board.db").read_text(), "current")
            self.assertTrue(Path(home, ".bot_board/board.db").exists())
