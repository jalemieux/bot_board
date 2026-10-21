"""#180: a fresh install starts one default bot, in the admin workspace, with a harness that
is here and logged in; or says exactly what to do. Run with python -m unittest discover -s tests."""
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from fastapi.testclient import TestClient     # noqa: E402
from app import db, main, workspaces          # noqa: E402
from test_keeper_creds import load_keeper     # noqa: E402

SOURCE = (ROOT / "bin/bot").read_text()
BOARD = "http://board.invalid:8080"


class Install(unittest.TestCase):
    """A piped install against a stand-in checkout: its bin/keeper answers `harnesses` with
    the rows a test gives, its bin/bot plays `keeper install` and writes the keeper's note."""

    def install(self, rows, *args, note=None, tty=None, installed=False, saved_bot=False, env=None):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            src = home / "src"
            (src / ".git").mkdir(parents=True)
            (src / "bin").mkdir()
            (src / "bin/keeper").write_text("import sys\nsys.stdout.write(%r)\n" % "".join("\t".join(r) + "\n" for r in rows))
            (src / "bin/bot").write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n'
                                         + (f'echo "# default bot: {note}" >> "$BOT_CONFIG"\n' if note else ""))
            (src / "bin/bot").chmod(0o755)
            if installed:     # a box that has had a keeper: its token is there
                (home / ".config/bot_board").mkdir(parents=True)
                (home / ".config/bot_board/keeper-box.token").write_text("t\n")
            if saved_bot:
                state = home / ".config/bot_board/bots/ada"
                state.mkdir(parents=True, exist_ok=True)
                (state / "harness").write_text("claude\n")
            if tty is not None:
                (home / "tty").write_text(tty)
            (home / "commands").mkdir()
            (home / "commands/git").write_text("#!/usr/bin/env bash\nexit 0\n")
            (home / "commands/git").chmod(0o755)
            config = home / "config"
            r = subprocess.run(["bash", "-s", "--", "install", *args], input=SOURCE, capture_output=True, text=True,
                               timeout=20, env={**os.environ, "HOME": tmp, "BOT_CONFIG": str(config),
                                                "BOT_BOARD_DIR": str(home), "BOARD": BOARD, "BOT_INSTALL_WAIT": "1",
                                                "PATH": str(home / "commands") + os.pathsep + os.environ["PATH"],
                                                "BOT_TTY": str(home / ("tty" if tty is not None else "none")),
                                                **(env or {})})
            self.assertEqual(r.returncode, 0, r.stderr)
            return r, config.read_text()

    CLAUDE_OK = [("claude", "ok", ""), ("codex", "missing", "not logged in"), ("copilot", "absent", ""),
                 ("opencode", "absent", "")]

    def test_no_terminal_takes_the_suggestion_and_says_what_started(self):
        r, config = self.install(self.CLAUDE_OK, note="@ada (claude) in workspace admin, started", env={"BOT_HANDLE": "ada"})
        self.assertEqual(r.stdout.splitlines(), ["keeper", "install"])
        self.assertIn('KEEPER_DEFAULT_BOT="claude"', config)
        self.assertIn('KEEPER_DEFAULT_HANDLE="ada"', config)
        self.assertIn("no terminal to ask on: starting a default bot with claude (logged in)", r.stderr)
        self.assertIn(f"started @ada (claude) in workspace admin; manage it at {BOARD}/fleet", r.stderr)

    def test_the_terminal_is_asked(self):
        r, config = self.install(self.CLAUDE_OK, tty="\n")
        self.assertIn("Start a default bot with claude (logged in)? [Y/n/other]", r.stderr)
        self.assertIn('KEEPER_DEFAULT_BOT="claude"', config)
        r, config = self.install(self.CLAUDE_OK, tty="n\n")
        self.assertNotIn("KEEPER_DEFAULT_BOT", config)
        rows = self.CLAUDE_OK[:3] + [("opencode", "ok", "")]
        r, config = self.install(rows, tty="other\nopencode\n")
        self.assertIn("Which harness (claude, codex, opencode)?", r.stderr)
        self.assertIn('KEEPER_DEFAULT_BOT="opencode"', config)
        r, config = self.install(self.CLAUDE_OK, tty="codex\n")
        self.assertNotIn("KEEPER_DEFAULT_BOT", config)
        self.assertIn("codex is installed but not logged in: run codex login, then rerun install", r.stderr)

    def test_logged_out_or_missing_harness_starts_nothing_and_says_what_to_do(self):
        r, config = self.install([("claude", "missing", "not logged in"), ("codex", "absent", ""),
                                  ("copilot", "absent", ""), ("opencode", "absent", "")])
        self.assertNotIn("KEEPER_DEFAULT_BOT", config)
        self.assertIn("claude is installed but not logged in: run claude, then /login, then rerun install", r.stderr)
        r, config = self.install([(h, "absent", "") for h in ("claude", "codex", "copilot", "opencode")])
        self.assertIn("no harness found: install Claude Code, Codex, Copilot or OpenCode, then rerun install"
                      f" or use + Add a bot on {BOARD}/fleet", r.stderr)

    def test_waiting_for_a_sign_in_says_so(self):
        r, _ = self.install(self.CLAUDE_OK, note="waiting: no board admin yet")
        self.assertIn(f"open {BOARD} and sign in; your bot (claude) starts then", r.stderr)
        self.assertIn("first sign-in creates Admin > General", r.stderr)
        self.assertIn(f"wait about 15 seconds, then check {BOARD}/fleet", r.stderr)
        self.assertIn(f"post in Admin > General ({BOARD}/c/admin)", r.stderr)
        self.assertIn("sign out and sign in again", r.stderr)

    def test_a_keeper_without_bots_still_offers_the_first_bot(self):
        r, config = self.install(self.CLAUDE_OK, installed=True, tty="y\n")
        self.assertIn("Start a default bot with claude (logged in)? [Y/n/other]", r.stderr)
        self.assertIn('KEEPER_DEFAULT_BOT="claude"', config)

    def test_an_update_with_saved_bots_or_no_bot_never_asks(self):
        r, config = self.install(self.CLAUDE_OK, installed=True, saved_bot=True, tty="y\n")
        self.assertNotIn("KEEPER_DEFAULT_BOT", config)
        self.assertNotIn("Start a default bot", r.stderr)
        r, config = self.install(self.CLAUDE_OK, "--no-bot", installed=True, tty="y\n")
        self.assertEqual(r.stdout.splitlines(), ["keeper", "install"])
        self.assertNotIn("KEEPER_DEFAULT_BOT", config)


    def test_a_value_that_would_run_as_shell_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "src/.git").mkdir(parents=True)
            (home / "src/bin").mkdir()
            (home / "commands").mkdir()
            (home / "commands/git").write_text("#!/usr/bin/env bash\nexit 0\n")
            (home / "commands/git").chmod(0o755)
            r = subprocess.run(["bash", "-s", "--", "install", "--no-bot"], input=SOURCE, capture_output=True, text=True,
                               timeout=20, env={**os.environ, "HOME": tmp, "BOT_CONFIG": str(home / "config"),
                                                "BOT_BOARD_DIR": str(home), "BOARD": BOARD,
                                                "PATH": str(home / "commands") + os.pathsep + os.environ["PATH"],
                                                "BOT_INVITE": 'x"; touch "$HOME/pwned'})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("BOT_INVITE may not contain", r.stderr)
            self.assertNotIn("pwned", (home / "config").read_text() if (home / "config").exists() else "")


class Board(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.keeper = self.register('keeper-box', 'keeper')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def report(self, bots=(), ask=None, operators=('jac',)):
        r = self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={
            'bots': list(bots), 'operators': list(operators), 'default_bot': ask})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def make_admin(self):
        self.register('jac', 'human')
        workspaces.ensure_admin()

    def test_waits_for_an_admin_workspace_then_starts_in_it(self):
        got = self.report(ask={'harness': 'claude', 'handle': 'ada'})
        self.assertIn('waiting', got['default_bot'])
        self.assertEqual(got['want'], [])
        self.make_admin()
        got = self.report(ask={'harness': 'claude', 'handle': 'ada'})
        self.assertEqual(got['default_bot'], {'handle': 'ada', 'harness': 'claude', 'created': True, 'workspace': 'admin'})
        [w] = got['want']
        self.assertEqual((w['handle'], w['harness'], w['run'], w['workspace']), ('ada', 'claude', True, 'admin'))
        again = self.report(bots=[{'handle': 'ada', 'harness': 'claude', 'status': 'waiting'}], ask={'harness': 'claude'})
        self.assertEqual(again['default_bot'], {'handle': 'ada', 'harness': '', 'created': False})

    def test_a_box_with_a_bot_gets_none_and_a_taken_name_gets_a_free_one(self):
        self.make_admin()
        got = self.report(bots=[{'handle': 'milo', 'harness': 'codex', 'status': 'waiting'}], ask={'harness': 'claude'})
        self.assertEqual(got['default_bot'], {'handle': 'milo', 'harness': '', 'created': False})
        db.conn().execute("DELETE FROM fleet_bots"); db.conn().commit()
        got = self.report(ask={'harness': 'claude', 'handle': 'jac'})     # a human's handle
        self.assertNotEqual(got['default_bot']['handle'], 'jac')
        self.assertTrue(got['default_bot']['created'])


class KeeperNote(unittest.TestCase):
    def test_the_boards_words_stay_one_comment_line(self):
        with tempfile.TemporaryDirectory() as home:
            k = load_keeper(home)
            cfg = Path(home) / 'config'
            cfg.write_text('KEEPER_DEFAULT_BOT="claude"\n')
            os.environ['BOT_CONFIG'] = str(cfg)
            try:
                k.default_bot_note({'waiting': 'sign in\ntouch /tmp/pwned\r'})
                k.default_bot_note({'handle': 'ada', 'harness': 'claude\nrm -rf ~', 'created': False})
            finally:
                os.environ.pop('BOT_CONFIG', None)
            lines = cfg.read_text().splitlines()
            self.assertTrue(all(l.startswith(('#', 'KEEPER_')) for l in lines), lines)
            self.assertIn('# default bot: @ada already on this box', lines)

    def test_started_drops_the_ask_and_waiting_keeps_it(self):
        with tempfile.TemporaryDirectory() as home:
            k = load_keeper(home)
            cfg = Path(home) / 'config'
            cfg.write_text('BOARD="x"\nKEEPER_DEFAULT_BOT="claude"\nKEEPER_DEFAULT_HANDLE="ada"\n')
            os.environ['BOT_CONFIG'] = str(cfg)
            try:
                k.default_bot_note({'waiting': 'no board admin yet'})
                self.assertIn('KEEPER_DEFAULT_BOT="claude"', cfg.read_text())
                self.assertIn('# default bot: waiting: no board admin yet', cfg.read_text())
                k.default_bot_note({'handle': 'ada', 'harness': 'claude', 'created': True, 'workspace': 'admin'})
                text = cfg.read_text()
                self.assertNotIn('KEEPER_DEFAULT', text)
                self.assertEqual(text.count('# default bot: '), 1)
                self.assertIn('# default bot: @ada (claude) in workspace admin, started', text)
            finally:
                os.environ.pop('BOT_CONFIG', None)


if __name__ == '__main__':
    unittest.main()
