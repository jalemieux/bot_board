"""#253: one checkout, ~/.bot_board/src, for the board and the bots, and one entry point,
~/.bot_board/bot_board, to run them. Run with python -m unittest discover -s tests.

Lifecycle runs the real thing in a temporary HOME (a venv, the board, the keeper's
install): slow and it needs pip to reach PyPI, so it runs with BOT_BOARD_E2E=1."""
import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


FAKE_BOT = """#!/usr/bin/env bash
echo "bot $*" >> "$CALLS"
case $1 in
  ps) printf 'ada\\tclaude\\twaiting\\n' ;;
  update) [ -z "${UPDATE_NEW:-}" ] || git -C "$(dirname "$0")/.." commit -q --allow-empty -m new ;;
esac
"""
FAKE_RUN = """#!/usr/bin/env bash
echo "run.sh $*" >> "$CALLS"
"""
# the service manager is the one thing the tests fake: it records what it was asked
FAKE_SERVICE = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$CALLS"
"""


class EntryPoint(unittest.TestCase):
    """bot_board acts on the board, the bots or both, and an update moves them together."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        home = self.home = Path(self.tmp.name)
        self.src = home / ".bot_board/src"
        (self.src / "bin").mkdir(parents=True)
        for path, text in ((self.src / "bin/bot", FAKE_BOT), (self.src / "run.sh", FAKE_RUN),
                           (home / "fakebin/systemctl", FAKE_SERVICE)):
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text); path.chmod(0o755)
        sh("git", "init", "-q", str(self.src)); sh("git", "commit", "-q", "--allow-empty", "-m", "v1", cwd=self.src)
        self.data = home / ".bot_board/board"
        self.cfg = home / ".config/bot_board"
        self.calls = home / "calls"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("BOT_")}, **GIT_ENV, "HOME": str(home),
                    "CALLS": str(self.calls), "PATH": f"{home / 'fakebin'}{os.pathsep}{os.environ['PATH']}"}

    def installed(self, board=True, bots=True):
        if board:
            (self.data / "venv").mkdir(parents=True); (self.data / "board.db").write_text("db")
        if bots:
            unit = self.home / ".config/systemd/user/bot-board-keeper.service"
            unit.parent.mkdir(parents=True); unit.write_text("[Unit]\n")
            self.cfg.mkdir(parents=True, exist_ok=True); (self.cfg / "config").write_text('BOARD="http://b:8080"\n')

    def run_bb(self, *args, **env):
        self.calls.write_text("")
        r = subprocess.run([str(ROOT / "bin/bot_board"), *args], env={**self.env, **env},
                           capture_output=True, text=True, timeout=30)
        return r, self.calls.read_text().splitlines()

    def test_uninstall_names_what_it_acts_on(self):
        r, _ = self.run_bb("uninstall")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("board, bots or all", r.stderr)

    def test_a_bare_install_installs_the_board_and_the_bots_beside_it(self):
        r, calls = self.run_bb("install")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["run.sh up", "bot install"])
        self.assertEqual(os.readlink(self.home / ".bot_board/bot_board"), str(self.src / "bin/bot_board"))

    def test_without_a_component_it_acts_on_what_is_installed(self):
        self.installed(board=False)
        r, calls = self.run_bb("restart")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["bot keeper install", "bot ps --tsv", "bot restart ada"], "no board here")
        self.installed(bots=False)
        r, calls = self.run_bb("stop")
        self.assertEqual(calls, ["run.sh stop", "systemctl --user stop bot-board-keeper", "bot ps --tsv", "bot stop ada"])

    def test_update_decides_by_the_commit_whether_to_restart(self):
        self.installed()
        r, calls = self.run_bb("update", UPDATE_NEW="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["bot update", "run.sh up", "bot keeper install"])
        r, calls = self.run_bb("update")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["bot update"], "nothing new: nothing restarted")
        self.assertIn("nothing new", r.stdout)

    def test_uninstall_keeps_the_data_unless_purged(self):
        self.installed()
        r, calls = self.run_bb("uninstall", "bots")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls[0], "bot keeper uninstall")
        self.assertTrue((self.cfg / "config").exists(), "identities and sessions stay")
        self.assertTrue((self.data / "board.db").exists(), "the board is not touched")
        r, calls = self.run_bb("uninstall", "board")
        self.assertEqual(calls, ["run.sh stop"])
        self.assertTrue((self.data / "board.db").exists())
        self.assertFalse((self.data / "venv").exists())


@unittest.skipUnless(os.environ.get("BOT_BOARD_E2E") == "1", "set BOT_BOARD_E2E=1: builds a venv, needs PyPI")
class Lifecycle(unittest.TestCase):
    """Install all, update to a new commit, update with nothing new, uninstall --purge: for real,
    in a temporary HOME, from a local bare repo. Only the service manager is faked, and the
    coding harnesses are kept out of HOME."""

    def test_install_update_and_uninstall_leave_nothing_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp); home = tmp / "home"; home.mkdir()
            repo = tmp / "repo.git"; seed = tmp / "seed"; fakebin = tmp / "fakebin"; calls = tmp / "calls"
            sh("git", "clone", "-q", "--bare", "--no-local", str(ROOT), str(repo))
            branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=ROOT)
            sh("git", "clone", "-q", "-b", branch, str(repo), str(seed))
            fakebin.mkdir()
            for name in ("systemctl", "launchctl", "loginctl"):
                (fakebin / name).write_text(FAKE_SERVICE); (fakebin / name).chmod(0o755)
            # the coding harnesses are not ours: a login check by a real one writes its own
            # files into HOME, so here none is installed
            for name in ("claude", "codex", "opencode", "copilot"):
                (fakebin / name).write_text("#!/bin/sh\nexit 127\n"); (fakebin / name).chmod(0o755)
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
            env = {**{k: v for k, v in os.environ.items() if not k.startswith(("BOT_", "BOARD"))}, **GIT_ENV,
                   "HOME": str(home), "PATH": f"{fakebin}{os.pathsep}{os.environ['PATH']}", "CALLS": str(calls),
                   "PORT": str(port), "BOT_BOARD_REPO": f"file://{repo}", "BOT_BOARD_REF": branch,
                   "BOT_BOARD_NO_DEFAULT_BOT": "1", "PIP_NO_CACHE_DIR": "1", "XDG_CACHE_HOME": str(tmp / "cache")}
            bb = home / ".bot_board/bot_board"

            def run(*cmd, stdin=None):
                calls.write_text("")
                r = subprocess.run(cmd, env=env, input=stdin, capture_output=True, text=True, timeout=600)
                self.assertEqual(r.returncode, 0, f"{cmd}: {r.stdout}\n{r.stderr}")
                return r, calls.read_text()

            def healthz():
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as resp:
                    return json.load(resp)

            try:
                # 1. the quick start: bin/bot_board piped into bash, nothing on the machine yet
                _, service = run("bash", "-s", "--", "install", stdin=(ROOT / "bin/bot_board").read_text())
                self.assertTrue(bb.is_symlink())
                first = sh("git", "rev-parse", "HEAD", cwd=home / ".bot_board/src")
                self.assertEqual(healthz()["commit"], first)
                self.assertIn("restart bot-board-keeper", service)
                self.assertIn("http://127.0.0.1:%d" % port, (home / ".config/bot_board/config").read_text())

                # 2. a new commit: the board comes back on it and the keeper is reinstalled
                (seed / "NEW").write_text("x"); sh("git", "add", "NEW", cwd=seed)
                sh("git", "commit", "-q", "-m", "new", cwd=seed); sh("git", "push", "-q", "origin", branch, cwd=seed)
                pid = (home / ".bot_board/board/board.pid").read_text()
                r, service = run(str(bb), "update")
                second = sh("git", "rev-parse", "HEAD", cwd=seed)
                self.assertEqual(healthz()["commit"], second, r.stdout)
                self.assertIn("restart bot-board-keeper", service)
                self.assertNotEqual((home / ".bot_board/board/board.pid").read_text(), pid)

                # 3. nothing new: nothing restarts
                pid = (home / ".bot_board/board/board.pid").read_text()
                r, service = run(str(bb), "update")
                self.assertIn("nothing new", r.stdout)
                self.assertEqual(service, "")
                self.assertEqual((home / ".bot_board/board/board.pid").read_text(), pid)

                # 4. uninstall all --purge: nothing left
                run(str(bb), "uninstall", "all", "--purge")
                time.sleep(1)
                with self.assertRaises(OSError):
                    healthz()
                self.assertEqual(sorted(str(p.relative_to(home)) for p in home.rglob("*")), [])
            finally:
                pidfile = home / ".bot_board/board/board.pid"
                if pidfile.exists():
                    subprocess.run(["kill", pidfile.read_text().strip()])


if __name__ == "__main__":
    unittest.main()
