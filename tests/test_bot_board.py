"""#253: one checkout, ~/.bot_board/src, for the board and the bots, and one entry point,
~/.bot_board/bot_board, to run them. Run with python -m unittest discover -s tests."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


class Migration(unittest.TestCase):
    """run.sh moves a board from its own clone (~/.bot_board/board/src) to the one checkout."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        origin = self.home / "origin.git"
        sh("git", "init", "-q", "--bare", "-b", "main", str(origin))
        seed = self.home / "seed"
        sh("git", "clone", "-q", str(origin), str(seed))
        (seed / "bin").mkdir()
        (seed / "bin/bot_board").write_text("#!/bin/sh\n"); (seed / "bin/bot_board").chmod(0o755)
        sh("git", "add", "-A", cwd=seed); sh("git", "commit", "-q", "-m", "v1", cwd=seed)
        sh("git", "push", "-q", "origin", "main", cwd=seed)
        self.origin = origin
        self.old = self.home / ".bot_board/board/src"
        self.src = self.home / ".bot_board/src"
        sh("git", "clone", "-q", str(origin), str(self.old))
        (self.home / ".bot_board/board/board.db").write_text("db")

    def migrate(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BOT_BOARD_")}
        return subprocess.run(["bash", "-s", "--", "migrate"], input=(ROOT / "run.sh").read_text(),
                               env={**env, "HOME": str(self.home)}, capture_output=True, text=True, timeout=30)

    def test_the_boards_clone_becomes_the_one_checkout(self):
        r = self.migrate()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.old.exists())
        self.assertTrue((self.src / ".git").is_dir())
        self.assertEqual((self.home / ".bot_board/board/board.db").read_text(), "db", "the data stays")
        self.assertEqual(os.readlink(self.home / ".bot_board/bot_board"), str(self.src / "bin/bot_board"))

    def test_beside_bot_installs_checkout_the_boards_clone_goes(self):
        sh("git", "clone", "-q", str(self.origin), str(self.src))
        (self.src / "mine").write_text("the keeper's checkout is not touched")
        r = self.migrate()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.old.exists())
        self.assertTrue((self.src / "mine").exists())

    def test_a_clone_with_work_in_it_is_never_moved_or_removed(self):
        for change in ("local", "unpushed"):
            with self.subTest(change=change):
                if change == "local":
                    (self.old / "bin/bot_board").write_text("#!/bin/sh\n# edited\n")
                else:
                    sh("git", "checkout", "-q", "--", ".", cwd=self.old)
                    (self.old / "new").write_text("x"); sh("git", "add", "new", cwd=self.old)
                    sh("git", "commit", "-q", "-m", "not pushed", cwd=self.old)
                r = self.migrate()
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("local changes or commits that are not on its origin", r.stderr)
                self.assertTrue((self.old / ".git").is_dir())
                self.assertFalse(self.src.exists())


FAKE_BOT = """#!/usr/bin/env bash
echo "bot $*" >> "$CALLS"
case $1 in
  ps) printf 'ada\\tclaude\\twaiting\\n' ;;
  update) echo "${UPDATE_SAYS:-keeper: updated aaa -> bbb (restart the keeper to run it: bot keeper install)}" ;;
esac
"""
FAKE_RUN = """#!/usr/bin/env bash
echo "run.sh $*" >> "$CALLS"
"""
FAKE_SYSTEMCTL = """#!/usr/bin/env bash
echo "systemctl $*" >> "$CALLS"
"""


class EntryPoint(unittest.TestCase):
    """bot_board acts on the board, the bots or both, and an update moves them together."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        home = self.home = Path(self.tmp.name)
        self.src = home / ".bot_board/src"
        (self.src / "bin").mkdir(parents=True)
        for path, text in ((self.src / "bin/bot", FAKE_BOT), (self.src / "run.sh", FAKE_RUN),
                           (home / "fakebin/systemctl", FAKE_SYSTEMCTL)):
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text); path.chmod(0o755)
        self.data = home / ".bot_board/board"
        self.cfg = home / ".config/bot_board"
        self.calls = home / "calls"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("BOT_")}, "HOME": str(home),
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

    def test_install_and_uninstall_name_what_they_act_on(self):
        r, _ = self.run_bb("uninstall")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("board, bots or all", r.stderr)

    def test_without_a_component_it_acts_on_what_is_installed(self):
        self.installed(board=False)
        r, calls = self.run_bb("restart")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["bot keeper install", "bot ps --tsv", "bot restart ada"], "no board here")
        self.installed(bots=False)
        r, calls = self.run_bb("stop")
        self.assertEqual(calls, ["run.sh stop", "systemctl --user stop bot-board-keeper", "bot ps --tsv", "bot stop ada"])

    def test_update_pulls_once_then_restarts_the_board_and_the_keeper(self):
        self.installed()
        r, calls = self.run_bb("update")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["bot update", "run.sh up", "bot keeper install"])
        r, calls = self.run_bb("update", UPDATE_SAYS="keeper: up to date")
        self.assertEqual(calls, ["bot update"], "nothing new: nothing restarted")

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
        r, _ = self.run_bb("uninstall", "all", "--purge")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.data.exists())
        self.assertFalse(self.cfg.exists())


if __name__ == "__main__":
    unittest.main()
