"""What `run.sh up` and `run.sh update` print (#766): a few lines, with the rest in the README. The board is not started: the
functions that start it are replaced, so the test needs no venv and no network. Run with python -m unittest discover -s tests."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RunOutput(unittest.TestCase):
    def run_sh(self, command, home, env=None):
        script = (ROOT / "run.sh").read_text().replace('case "$CMD" in', 'start(){ :; }; healthy(){ return 0; }; fetch(){ :; }\ncase "$CMD" in', 1)
        path = Path(home) / "run-test.sh"
        path.write_text(script)
        full = {**os.environ, "HOME": home, "BOT_BOARD_SRC": str(ROOT), **(env or {})}
        for k in ("PORT", "BIND", "BOT_BOARD_DIR"):      # the test's own settings only
            if not env or k not in env:
                full.pop(k, None)
        r = subprocess.run(["bash", str(path), command], capture_output=True, text=True, env=full, cwd=home)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_up_prints_the_address_what_was_installed_how_it_runs_how_to_manage_it_and_the_next_step(self):
        with tempfile.TemporaryDirectory() as home:
            out = self.run_sh("up", home, {"BOT_BOARD_SRC": f"{home}/.bot_board/src", "BOT_BOARD_DIR": f"{home}/.bot_board/board"})
        self.assertEqual(out, "bot_board is up at http://127.0.0.1:8080\n"
                              "  Installed: the board, in ~/.bot_board/board (data: board.db, back it up; log: board.log)\n"
                              "  Runs:      in the background until you stop it or restart this machine\n"
                              "  Manage:    ~/.bot_board/bot_board status|stop|restart|update\n"
                              "\n"
                              "Next:   open http://127.0.0.1:8080 and sign in with any handle\n")

    def test_up_names_the_real_address_when_the_port_and_bind_are_set(self):
        with tempfile.TemporaryDirectory() as home:
            out = self.run_sh("up", home, {"PORT": "9000", "BIND": "0.0.0.0", "BOT_BOARD_DIR": f"{home}/b"})
        self.assertTrue(out.startswith("bot_board is up at http://127.0.0.1:9000\n"), out)
        self.assertIn("open http://127.0.0.1:9000 and sign in", out)

    def test_update_prints_one_line_with_the_commit_and_the_address(self):
        with tempfile.TemporaryDirectory() as home:
            repo = Path(home) / "checkout"      # a checkout of one's own: run.sh updates the board here, and does not hand over to the managed one
            repo.mkdir()
            (repo / "Dockerfile").write_text("")
            for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
                subprocess.run(["git", "-C", str(repo)] + cmd, check=True, capture_output=True)
            out = self.run_sh("update", str(repo), {"HOME": home, "BOT_BOARD_SRC": f"{home}/.bot_board/src"})
        self.assertRegex(out, r"^bot_board updated to [0-9a-f]{7,} and running at http://127\.0\.0\.1:8080\n$")

    def test_the_managed_update_hands_the_board_restart_to_run_sh_update_with_a_flag_that_stops_the_loop(self):
        # bin/bot_board update calls `run.sh update`; run.sh update hands over to bin/bot_board update unless that flag is set
        bb = (ROOT / "bin" / "bot_board").read_text()
        run = (ROOT / "run.sh").read_text()
        self.assertIn('BOT_BOARD_UPDATING=1 board update', bb)
        self.assertIn('[ -z "${BOT_BOARD_UPDATING:-}" ]', run)

    def test_what_left_the_output_is_in_the_readme(self):
        readme = (ROOT / "README.md").read_text()
        for piece in ("install bots", "single-user", "A handle is", "sign out and sign in", "@reboot", "start board"):
            self.assertIn(piece, readme, piece)
        self.assertIn('*"no crontab"*', readme)       # an unreadable crontab is left alone, not replaced by one line
        self.assertNotIn("crontab -l 2>/dev/null;", readme)
        run = (ROOT / "run.sh").read_text()
        for gone in ("/fleet", "single-user", "crontab", "sign out"):
            self.assertNotIn(gone, run, gone)


if __name__ == "__main__":
    unittest.main()
