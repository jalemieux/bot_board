"""#878: the question "Run bots in containers?" at install, and `bot_board containers` later (deploy/containers). Fake docker, podman and
uname on PATH; the terminal is a file. Run with python -m unittest discover -s tests."""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = str(ROOT / "deploy" / "containers")


def script(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class Containers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.bin = t / "bin"
        self.bin.mkdir()
        self.cfg = t / "home" / "config"
        self.cfg.parent.mkdir()
        self.tty = t / "tty"

    def runtime(self, name="docker", works=True):
        script(self.bin / name, f"#!/bin/sh\n[ \"$1\" = info ] && exit {0 if works else 1}\nexit 0\n")

    def uname(self, system):
        script(self.bin / "uname", f"#!/bin/sh\n[ \"$1\" = -s ] && echo {system} && exit 0\nexec /usr/bin/uname \"$@\"\n")

    def run_it(self, mode="install", answer=None, **env):
        if answer is not None:
            self.tty.write_text(answer + "\n")
        e = {k: v for k, v in os.environ.items() if k not in ("BOT_BOARD_CONTAINERS", "BOT_RUNTIME", "BOT_HARNESS")}
        e.update(PATH=str(self.bin) + os.pathsep + "/usr/bin:/bin", HOME=str(self.cfg.parent), BOT_CONFIG=str(self.cfg),
                 BOT_TTY=str(self.tty) if answer is not None else "/nonexistent-tty", **env)
        return subprocess.run(["bash", SCRIPT, mode], env=e, capture_output=True, text=True, timeout=30)

    def config(self):
        return self.cfg.read_text() if self.cfg.exists() else ""

    def test_with_a_runtime_and_no_terminal_the_default_is_yes_and_it_is_written(self):
        self.runtime()
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('BOT_RUNTIME="docker"', self.config())
        self.assertIn("docker is here and works", r.stderr)

    def test_with_no_runtime_and_no_terminal_the_default_is_no_and_nothing_hangs(self):
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("BOT_RUNTIME", self.config())
        self.assertIn("no container runtime", r.stderr)

    def test_a_person_is_asked_with_the_isolation_notice_and_can_say_no(self):
        self.runtime()
        r = self.run_it(answer="n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("BOT_RUNTIME", self.config())
        self.assertIn("Run bots in containers? [Y/n]", r.stderr)
        self.assertIn("as you, with your user's access", r.stderr, "what no gives up is said where the question is asked")

    def test_an_empty_answer_takes_the_default_and_yes_is_yes(self):
        self.runtime("podman")
        self.run_it(answer="")
        self.assertIn('BOT_RUNTIME="podman"', self.config())
        self.cfg.unlink()
        self.run_it(answer="yes")
        self.assertIn('BOT_RUNTIME="podman"', self.config())

    def test_a_yes_with_no_runtime_prints_how_to_get_one_and_stops_without_writing(self):
        for answer_env in ({"BOT_BOARD_CONTAINERS": "1"},):
            r = self.run_it(**answer_env)
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertNotIn("BOT_RUNTIME", self.config())
            self.assertIn("run the install again", r.stderr)
            self.assertIn("docker.io", r.stderr)

    def test_on_a_mac_the_tip_names_docker_desktop(self):
        self.uname("Darwin")
        r = self.run_it(BOT_BOARD_CONTAINERS="1")
        self.assertEqual(r.returncode, 3)
        self.assertIn("Docker Desktop", r.stderr)

    def test_the_switches_win_over_the_default(self):
        self.runtime()
        self.assertEqual(self.run_it(BOT_BOARD_CONTAINERS="0").returncode, 0)
        self.assertNotIn("BOT_RUNTIME", self.config())
        self.run_it(BOT_BOARD_CONTAINERS="1")
        self.assertIn('BOT_RUNTIME="docker"', self.config())
        self.assertEqual(self.run_it(BOT_BOARD_CONTAINERS="maybe").returncode, 2)

    def test_a_claude_bot_on_a_mac_defaults_to_no_with_the_keychain_reason(self):
        self.runtime()
        self.uname("Darwin")
        r = self.run_it(BOT_HARNESS="claude")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("BOT_RUNTIME", self.config())
        self.assertIn("Keychain", r.stderr)
        r = self.run_it(BOT_HARNESS="claude", BOT_BOARD_CONTAINERS="1")          # a person who insists gets it
        self.assertIn('BOT_RUNTIME="docker"', self.config())

    def test_the_later_command_defaults_to_what_is_set_and_no_removes_the_line(self):
        self.runtime()
        self.cfg.write_text('BOARD="http://b:8080"\nBOT_RUNTIME="docker"\n')
        r = self.run_it("ask", answer="")
        self.assertIn('BOT_RUNTIME="docker"', self.config())
        self.assertIn("[Y/n]", r.stderr)
        r = self.run_it("ask", answer="n")
        self.assertNotIn("BOT_RUNTIME", self.config())
        self.assertIn('BOARD="http://b:8080"', self.config(), "the rest of the config is kept")
        self.assertIn("bot_board restart bots", r.stderr)

    def test_the_loopback_board_note_is_in_the_question(self):
        self.runtime()
        r = self.run_it(answer="y", BOARD="http://127.0.0.1:8080")
        self.assertIn("127.0.0.1", r.stderr)


if __name__ == "__main__":
    unittest.main()
