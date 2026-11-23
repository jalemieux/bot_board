"""#253: one checkout, ~/.bot_board/src, for the board and the bots, and one entry point,
~/.bot_board/bot_board, to run them. Run with python -m unittest discover -s tests.

Lifecycle runs the real thing in a temporary HOME (a venv, the board, the keeper's
install): slow and it needs pip to reach PyPI, so it runs with BOT_BOARD_E2E=1."""
import json
import os
import shutil
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
        (self.src / "bin/bot_board").write_text((ROOT / "bin/bot_board").read_text())   # install goes on from it
        (self.src / "bin/bot_board").chmod(0o755)
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

    def expose_env(self, board="http://127.0.0.1:8080"):
        """A board and its keeper on this machine, the keeper pointing at it; run.sh records the BIND it was asked for."""
        self.installed()
        (self.cfg / "config").write_text(f'BOARD="{board}"\nKEEPER_HOST="m1"\n')
        (self.src / "run.sh").write_text('#!/usr/bin/env bash\necho "run.sh $* BIND=${BIND:-}" >> "$CALLS"\n')
        self.fake_curl(0)

    def fake_curl(self, code):
        """The board's health check: 0 answers, anything else does not."""
        curl = self.home / "fakebin/curl"
        curl.write_text(f"#!/usr/bin/env bash\nexit {code}\n"); curl.chmod(0o755)

    def test_expose_moves_the_board_and_this_machines_keeper_together(self):
        # #796: the board's address, the keeper's BOARD and the printed block change together, in one command
        self.expose_env()
        r, calls = self.run_bb("expose", "100.64.0.9")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["run.sh up BIND=100.64.0.9", "bot keeper install", "bot ps --tsv", "bot restart ada"])
        cfg = (self.cfg / "config").read_text()
        self.assertIn('BOARD="http://100.64.0.9:8080"', cfg)
        self.assertNotIn("127.0.0.1", cfg)
        self.assertIn('KEEPER_HOST="m1"', cfg)                      # nothing else in the config is touched
        self.assertIn("Open:         http://100.64.0.9:8080", r.stdout)
        self.assertIn("its keeper reports to http://100.64.0.9:8080", r.stdout)
        self.assertIn("http://100.64.0.9:8080/machines", r.stdout)
        self.assertIn("anyone who can reach 100.64.0.9 on port 8080 can use the board", r.stdout)
        self.assertIn("BOT_BOARD_INVITE_CODE", r.stdout)

    def test_expose_a_second_time_changes_nothing(self):
        self.expose_env("http://100.64.0.9:8080")
        r, calls = self.run_bb("expose", "100.64.0.9", BIND="100.64.0.9")      # the board already listens there (run.sh kept it)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, [])
        self.assertIn("nothing changed", r.stdout)
        self.assertEqual((self.cfg / "config").read_text().count("BOARD="), 1)

    def test_expose_refuses_every_interface_unless_asked_and_a_keeper_follows_on_loopback(self):
        self.expose_env()
        r, calls = self.run_bb("expose", "0.0.0.0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--all-interfaces", r.stderr)
        self.assertEqual(calls, [])
        r, calls = self.run_bb("expose", "0.0.0.0", "--all-interfaces")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls[0], "run.sh up BIND=0.0.0.0")
        self.assertIn('BOARD="http://127.0.0.1:8080"', (self.cfg / "config").read_text())   # this machine reaches it on loopback

    def test_expose_needs_a_flag_for_a_public_address_and_not_for_a_private_one(self):
        self.expose_env()
        for ok in ("10.1.2.3", "192.168.1.5", "172.20.0.4", "100.64.0.9", "100.127.255.1", "169.254.1.1", "my-box.tailnet.ts.net"):
            r, _ = self.run_bb("expose", ok)
            self.assertEqual(r.returncode, 0, (ok, r.stderr))
        (self.cfg / "config").write_text('BOARD="http://127.0.0.1:8080"\n')
        for bad in ("8.8.8.8", "100.128.0.1", "172.32.0.1", "11.0.0.1"):
            r, calls = self.run_bb("expose", bad)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("public address", r.stderr)
            self.assertEqual(calls, [], bad)
        r, calls = self.run_bb("expose", "8.8.8.8", "--public")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls[0], "run.sh up BIND=8.8.8.8")

    def test_expose_says_so_and_leaves_the_keeper_when_the_board_does_not_answer(self):
        self.expose_env()
        self.fake_curl(22)
        r, calls = self.run_bb("expose", "100.64.0.9", BOT_BOARD_EXPOSE_WAIT="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did not answer at http://100.64.0.9:8080", r.stderr)
        self.assertIn("bot_board expose 127.0.0.1", r.stderr)
        self.assertEqual(calls, ["run.sh up BIND=100.64.0.9"], "the keeper is not moved to an address that does not answer")
        self.assertIn('BOARD="http://127.0.0.1:8080"', (self.cfg / "config").read_text())

    def native_forgejo(self, allowed="127.0.0.1"):
        """A Forgejo beside the board: its settings allow webhooks to one host, and its control script records its calls."""
        forgejo = self.home / "forgejo"
        ini = forgejo / "work/custom/conf/app.ini"
        ini.parent.mkdir(parents=True)
        ini.write_text(f"[webhook]\nALLOWED_HOST_LIST = {allowed}\n[log]\nROOT_PATH = /x\n")
        ctl = self.src / "deploy/forgejo-native"
        ctl.parent.mkdir(parents=True, exist_ok=True)
        ctl.write_text('#!/usr/bin/env bash\necho "forgejo-native $1 BOARD=$BOARD" >> "$CALLS"\n'); ctl.chmod(0o755)
        return forgejo, ini

    def test_expose_lets_a_native_forgejo_deliver_to_the_new_address_and_says_what_is_left(self):
        # a board with a Forgejo beside it has a fourth address: where Forgejo may deliver its webhook
        self.expose_env()
        forgejo, ini = self.native_forgejo()
        ini.chmod(0o600)
        old_umask = os.umask(0o022)       # a loose umask must not loosen the file
        self.addCleanup(os.umask, old_umask)
        r, calls = self.run_bb("expose", "100.64.0.9", FORGEJO_DIR=str(forgejo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ALLOWED_HOST_LIST = 100.64.0.9\n", ini.read_text())
        self.assertIn("[log]\nROOT_PATH = /x", ini.read_text(), "the rest of the settings stay")
        self.assertEqual(oct(ini.stat().st_mode & 0o777), "0o600", "app.ini holds secret keys: owner-only, whatever the umask")
        self.assertIn("forgejo-native stop BOARD=http://100.64.0.9:8080", calls)
        self.assertIn("forgejo-native start BOARD=http://100.64.0.9:8080", calls)
        # forge-setup takes its native path only with the environment forgejo-native env gives (FORGEJO_BIN, FORGE ...)
        self.assertIn('eval "$(BOARD=http://100.64.0.9:8080 bash ', r.stdout)
        self.assertIn("deploy/forgejo-native env)\"; BOARD=http://100.64.0.9:8080 bash ", r.stdout)
        self.assertIn("deploy/forge-setup", r.stdout)
        self.assertIn("it asks for your handle on the board", r.stdout)
        self.assertIn("events stop reaching threads", r.stdout)

    def forge_fakes(self, humans=("jac",)):
        """The board answers its agents list and a sign-in; forgejo-native prints its environment; forge-setup records itself."""
        agents = json.dumps({"agents": [{"handle": h, "kind": "human"} for h in humans] + [{"handle": "ada", "kind": "claude-code"}]})
        curl = self.home / "fakebin/curl"
        curl.write_text('#!/usr/bin/env bash\ncase "$*" in\n *" -d "*) cat >/dev/null; echo \'{"token": "tok-human"}\' ;;\n'
                        f" */api/agents*) echo '{agents}' ;;\nesac\nexit 0\n")
        curl.chmod(0o755)
        ctl = self.src / "deploy/forgejo-native"
        ctl.write_text('#!/usr/bin/env bash\necho "forgejo-native $1 BOARD=$BOARD" >> "$CALLS"\n[ "$1" != env ] || echo "export FORGEJO_BIN=/x/forgejo"\n')
        setup = self.src / "deploy/forge-setup"
        setup.write_text('#!/usr/bin/env bash\necho "forge-setup BOARD=$BOARD TOKEN=${BOARD_HUMAN_TOKEN:+given} BIN=${FORGEJO_BIN:-}" >> "$CALLS"\n')
        setup.chmod(0o755)

    def test_expose_points_the_forge_webhook_at_the_new_address_itself_when_the_board_has_one_human(self):
        # #805: no command to paste: it signs in as the board's one person and runs forge-setup
        self.expose_env()
        forgejo, ini = self.native_forgejo()
        self.forge_fakes()
        r, calls = self.run_bb("expose", "100.64.0.9", FORGEJO_DIR=str(forgejo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("forge-setup BOARD=http://100.64.0.9:8080 TOKEN=given BIN=/x/forgejo", calls)
        self.assertIn("forge-setup pointed its site webhook at the new address (signed in as jac)", r.stdout)
        self.assertNotIn("events stop reaching threads", r.stdout)
        self.assertNotIn("tok-human", r.stdout + r.stderr, "the token is never printed")

    def test_expose_leaves_forge_setup_to_a_person_when_the_board_has_more_than_one_human(self):
        self.expose_env()
        forgejo, ini = self.native_forgejo()
        self.forge_fakes(humans=("jac", "sam"))
        r, calls = self.run_bb("expose", "100.64.0.9", FORGEJO_DIR=str(forgejo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse([c for c in calls if c.startswith("forge-setup")], calls)
        self.assertIn("events stop reaching threads", r.stdout)
        self.assertIn("it asks for your handle on the board", r.stdout)

    def test_expose_adds_the_allow_list_when_the_settings_have_none(self):
        self.expose_env()
        forgejo, ini = self.native_forgejo()
        ini.write_text("[webhook]\n[log]\nROOT_PATH = /x\n")
        r, _ = self.run_bb("expose", "100.64.0.9", FORGEJO_DIR=str(forgejo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(ini.read_text(), "[webhook]\nALLOWED_HOST_LIST = 100.64.0.9\n[log]\nROOT_PATH = /x\n")
        ini.write_text("[log]\nROOT_PATH = /x\n")
        (self.cfg / "config").write_text('BOARD="http://127.0.0.1:8080"\n')
        r, _ = self.run_bb("expose", "100.64.0.8", FORGEJO_DIR=str(forgejo))
        self.assertTrue(ini.read_text().endswith("[webhook]\nALLOWED_HOST_LIST = 100.64.0.8\n"), ini.read_text())

    def test_expose_leaves_forgejo_alone_when_it_already_allows_the_host_or_there_is_none(self):
        self.expose_env()
        forgejo, ini = self.native_forgejo(allowed="100.64.0.9")
        r, calls = self.run_bb("expose", "100.64.0.9", FORGEJO_DIR=str(forgejo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse([c for c in calls if c.startswith("forgejo-native")], calls)
        self.assertNotIn("site webhook", r.stdout)

    def test_expose_to_every_interface_leaves_forgejo_on_loopback(self):
        # Forgejo still reaches an all-interfaces board on 127.0.0.1, so its settings are right as they are
        self.expose_env()
        forgejo, ini = self.native_forgejo()
        r, calls = self.run_bb("expose", "0.0.0.0", "--all-interfaces", FORGEJO_DIR=str(forgejo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ALLOWED_HOST_LIST = 127.0.0.1\n", ini.read_text())
        self.assertFalse([c for c in calls if c.startswith("forgejo-native")], calls)

    def test_expose_back_to_loopback_puts_forgejo_back_too(self):
        self.expose_env("http://100.64.0.9:8080")
        forgejo, ini = self.native_forgejo(allowed="100.64.0.9")
        r, calls = self.run_bb("expose", "127.0.0.1", FORGEJO_DIR=str(forgejo), BIND="100.64.0.9")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ALLOWED_HOST_LIST = 127.0.0.1\n", ini.read_text())
        self.assertIn("still points at 100.64.0.9", r.stdout)

    def test_expose_leaves_a_keeper_that_reports_to_another_board_alone(self):
        self.expose_env("http://other-box:8080")
        r, calls = self.run_bb("expose", "100.64.0.9")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["run.sh up BIND=100.64.0.9"])
        self.assertIn('BOARD="http://other-box:8080"', (self.cfg / "config").read_text())
        self.assertNotIn("its keeper reports", r.stdout)

    def test_expose_needs_a_board_here_and_an_address(self):
        r, _ = self.run_bb("expose", "100.64.0.9")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nothing installed here", r.stderr + r.stdout)
        self.expose_env()
        r, calls = self.run_bb("expose")
        self.assertEqual(r.returncode, 1)
        self.assertIn("usage: bot_board expose <address>", r.stderr)
        r, _ = self.run_bb("expose", "bad address;rm")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(calls, [])

    def test_uninstall_names_what_it_acts_on(self):
        r, _ = self.run_bb("uninstall")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("board, bots or all", r.stderr)

    def test_a_bare_install_installs_the_board_and_the_bots_beside_it(self):
        r, calls = self.run_bb("install")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["run.sh up", "bot install"])
        self.assertEqual(os.readlink(self.home / ".bot_board/bot_board"), str(self.src / "bin/bot_board"))

    def test_install_runs_its_steps_quietly_and_prints_one_block(self):
        # #766: run.sh, bin/bot and the keeper keep their details for their logs under `bot_board install`; this prints one block
        (self.src / "run.sh").write_text('#!/usr/bin/env bash\necho "run.sh $* QUIET=${BOT_BOARD_QUIET:-}" >> "$CALLS"\n')
        (self.src / "bin/bot").write_text(
            '#!/usr/bin/env bash\necho "bot $* QUIET=${BOT_BOARD_QUIET:-}" >> "$CALLS"\n'
            'if [ "$1" = install ]; then mkdir -p "$HOME/.config/bot_board" "$HOME/.config/systemd/user"; : > "$HOME/.config/systemd/user/bot-board-keeper.service"; '
            'printf \'KEEPER_DEFAULT_BOT="claude"\\n# default bot: @ada (claude) in workspace admin, started\\n\' >> "$HOME/.config/bot_board/config"; fi\n')
        r, calls = self.run_bb("install", BOT_BOARD_FROM_SRC="1", BOARD="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["run.sh up QUIET=1", "bot install QUIET=1"])
        self.assertEqual(r.stdout, "bot_board is up at http://127.0.0.1:8080\n"
                                   "  Installed: the board, in ~/.bot_board/board (data: board.db, back it up; log: board.log)\n"
                                   "             a keeper (log: keeper.log) and a default bot, @ada (claude)\n"
                                   "             the bots run as you, with your user's access\n"
                                   "  Runs:      in the background until you stop it or restart this machine\n"
                                   "  Manage:    ~/.bot_board/bot_board status|stop|restart|update|uninstall\n"
                                   "\n"
                                   "Next:   open http://127.0.0.1:8080 and sign in with any handle\n")
        self.assertEqual(r.stderr, "")

    def test_the_install_block_says_when_the_default_bot_was_already_here_and_names_a_container(self):
        (self.src / "run.sh").write_text('#!/usr/bin/env bash\n:\n')
        (self.src / "bin/bot").write_text(
            '#!/usr/bin/env bash\nif [ "$1" = install ]; then mkdir -p "$HOME/.config/bot_board" "$HOME/.config/systemd/user"; : > "$HOME/.config/systemd/user/bot-board-keeper.service"; '
            'printf \'BOT_RUNTIME="docker"\\n# default bot: ada is on this box already\\n\' >> "$HOME/.config/bot_board/config"; fi\n')
        r, _ = self.run_bb("install", BOT_BOARD_FROM_SRC="1", BOARD="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("a keeper (log: keeper.log); a default bot, ada, was already here", r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 8, r.stdout)

    def test_the_install_block_keeps_what_the_bots_can_reach_in_every_state_of_the_default_bot(self):
        # #670: the isolation notice stays on the screen under the quiet install, whatever the keeper's note says
        notes = {"started": '# default bot: @ada (claude) in workspace admin, started\n', "waiting": "# default bot: waiting: sign in\n",
                 "already here": "# default bot: @ada already on this box\n", "no note": ""}
        cases = (('BOT_RUNTIME="docker"\n', "Linux", "the bots run in a container (they share this machine's network, so they can reach any service on 127.0.0.1)"),
                 ('BOT_RUNTIME="docker"\n', "Darwin", "the bots run in a container (they can also reach every port this Mac listens on)"),
                 ("", "Linux", "the bots run as you, with your user's access"))
        for name, note in notes.items():
            for runtime, uname, expected in cases:
                with self.subTest(note=name, uname=uname, runtime=bool(runtime)):
                    self.tearDown() if hasattr(self, "_started") else None
                    self.setUp(); self._started = True
                    (self.src / "run.sh").write_text('#!/usr/bin/env bash\n:\n')
                    (self.src / "bin/bot").write_text('#!/usr/bin/env bash\nif [ "$1" = install ]; then mkdir -p "$HOME/.config/bot_board" "$HOME/.config/systemd/user"; : > "$HOME/.config/systemd/user/bot-board-keeper.service"; '
                                                      "printf '%s' '" + 'KEEPER_DEFAULT_BOT="claude"\n' + runtime + note + "' >> \"$HOME/.config/bot_board/config\"; fi\n")
                    (self.home / "fakebin/uname").write_text(f"#!/bin/sh\necho {uname}\n"); (self.home / "fakebin/uname").chmod(0o755)
                    r, _ = self.run_bb("install", BOT_BOARD_FROM_SRC="1", BOARD="")
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertIn(expected, r.stdout)

    def test_the_keepers_default_bot_note_has_the_forms_its_two_readers_know(self):
        # the note in the config is written by bin/keeper and read by bin/bot (its outcome lines) and bin/bot_board (the block)
        keeper = (ROOT / "bin/keeper").read_text()
        for written in ('"# default bot: waiting: ', "in workspace {got.get('workspace') or 'admin'}, started", "already on this box"):
            self.assertIn(written, keeper)
        bot = (ROOT / "bin/bot").read_text()
        block = (ROOT / "bin/bot_board").read_text()
        for read in ("waiting:*)", "*started)"):
            self.assertIn(read, bot)
            self.assertIn(read, block)

    def test_the_install_block_says_when_there_is_no_keeper_service(self):
        # #549: a box with no systemd user manager: the block says so and how to run the keeper, and does not claim a keeper or a bot
        (self.src / "run.sh").write_text('#!/usr/bin/env bash\n:\n')
        (self.src / "bin/bot").write_text('#!/usr/bin/env bash\nexit 0\n')      # `bot install` ended cleanly (exit 75 of the keeper); no unit file
        r, _ = self.run_bb("install", BOT_BOARD_FROM_SRC="1", BOARD="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no keeper service: this machine has no systemd user manager. Run it yourself:", r.stdout)
        self.assertNotIn("a default bot", r.stdout)
        self.assertTrue(r.stdout.splitlines()[-1].startswith("Next:"))

    def test_stop_and_status_on_a_box_with_a_unit_file_and_no_systemd_do_not_fail_on_the_missing_command(self):
        # #549 (ken's note on PR #790): a unit file from an earlier install, and no systemctl on this machine
        if shutil.which("systemctl", path="/usr/bin:/bin"):
            self.skipTest("this machine has systemctl; the case is a box without it")
        self.installed(board=False)
        r, _ = self.run_bb("stop", "bots", PATH=f"{self.home / 'nobin'}{os.pathsep}/usr/bin{os.pathsep}/bin")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("systemctl: command not found", r.stderr)
        r, _ = self.run_bb("status", "bots", PATH=f"{self.home / 'nobin'}{os.pathsep}/usr/bin{os.pathsep}/bin")
        self.assertNotIn("command not found", r.stderr)

    def test_start_restart_and_update_treat_a_keeper_install_that_exits_75_as_done(self):
        # #549 (gina): the same exit 75 as `bin/bot install` (no systemd user manager), under `set -e` in start, restart and update
        self.installed(board=False)
        (self.src / "bin/bot").write_text('#!/usr/bin/env bash\necho "bot $*" >> "$CALLS"\ncase $1 in keeper) exit 75 ;; esac\n')
        for command in ("start", "restart"):
            r, calls = self.run_bb(command)
            self.assertEqual(r.returncode, 0, (command, r.stderr))
            self.assertIn("bot keeper install", calls)

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
        self.assertEqual(calls, ["bot update", "run.sh update", "bot keeper install"])
        r, calls = self.run_bb("update")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, ["bot update"], "nothing new: nothing restarted")
        self.assertIn("nothing new", r.stdout)

    def test_update_calls_run_sh_update_with_the_flag_that_stops_the_hand_back(self):
        self.installed()
        flag = self.home / "flag"
        run = self.src / "run.sh"
        run.write_text('#!/usr/bin/env bash\necho "run.sh $*" >> "$CALLS"\necho "${BOT_BOARD_UPDATING:-unset}" > "' + str(flag) + '"\n')
        run.chmod(0o755)
        r, calls = self.run_bb("update", UPDATE_NEW="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("run.sh update", calls)
        self.assertEqual(flag.read_text().strip(), "1")

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
        py = subprocess.run(["python3", "-c", "import sys; print(sys.version_info >= (3, 10))"],
                            capture_output=True, text=True).stdout.strip()
        if py != "True":
            self.skipTest("run.sh needs Python 3.10 or newer as python3 (macOS ships 3.9)")
        # the keeper's service is restarted by systemd on Linux, reloaded by launchd on macOS
        restarted = lambda calls: "restart bot-board-keeper" in calls or "launchctl bootstrap" in calls
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
            # files into HOME, so here none is installed; nor gh, whose login check the keeper runs
            for name in ("claude", "codex", "opencode", "copilot", "gh"):
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
                self.assertTrue(restarted(service), service)
                self.assertIn("http://127.0.0.1:%d" % port, (home / ".config/bot_board/config").read_text())

                # 2. a new commit: the board comes back on it and the keeper is reinstalled
                (seed / "NEW").write_text("x"); sh("git", "add", "NEW", cwd=seed)
                sh("git", "commit", "-q", "-m", "new", cwd=seed); sh("git", "push", "-q", "origin", branch, cwd=seed)
                pid = (home / ".bot_board/board/board.pid").read_text()
                r, service = run(str(bb), "update")
                second = sh("git", "rev-parse", "HEAD", cwd=seed)
                self.assertEqual(healthz()["commit"], second, r.stdout)
                self.assertTrue(restarted(service), service)
                self.assertNotEqual((home / ".bot_board/board/board.pid").read_text(), pid)

                # 3. nothing new: nothing restarts
                pid = (home / ".bot_board/board/board.pid").read_text()
                r, service = run(str(bb), "update")
                self.assertIn("nothing new", r.stdout)
                self.assertEqual(service, "")
                self.assertEqual((home / ".bot_board/board/board.pid").read_text(), pid)

                # 4. uninstall bots --purge tells the board first (#279): the box and its keeper are off it
                def api(path, token=None):
                    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
                    if token:
                        req.add_header("Authorization", f"Bearer {token}")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        return json.load(resp)
                human = urllib.request.Request(f"http://127.0.0.1:{port}/api/agents", method="POST",
                                               data=json.dumps({"handle": "op", "kind": "human"}).encode(),
                                               headers={"content-type": "application/json"})
                with urllib.request.urlopen(human, timeout=5) as resp:
                    op = json.load(resp)["token"]
                run(str(home / ".bot_board/src/bin/bot"), "keeper", "once")
                self.assertEqual(len(api("/api/fleet", op)["hosts"]), 1)
                r, _ = run(str(bb), "uninstall", "bots", "--purge")
                self.assertIn("is off", r.stdout)
                self.assertEqual(api("/api/fleet", op)["hosts"], [])
                self.assertEqual([a["handle"] for a in api("/api/agents", op)["agents"] if a["kind"] != "human"], [])

                # 5. uninstall all --purge: nothing left
                run(str(bb), "uninstall", "all", "--purge")
                time.sleep(1)
                with self.assertRaises(OSError):
                    healthz()
                # ~/Library is the Mac's own (every Mac has Library/LaunchAgents): uninstall leaves it
                self.assertEqual(sorted(str(p.relative_to(home)) for p in home.rglob("*")
                                        if p.relative_to(home).parts[0] != "Library"), [])
            finally:
                pidfile = home / ".bot_board/board/board.pid"
                if pidfile.exists():
                    subprocess.run(["kill", pidfile.read_text().strip()])


if __name__ == "__main__":
    unittest.main()
