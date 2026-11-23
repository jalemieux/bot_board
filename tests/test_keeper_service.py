"""The keeper's service install: what it prints alone, and where it writes it under `bot_board install` (#766). The service manager is a
stand-in that does nothing. Run with python -m unittest discover -s tests."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform.startswith("linux"), "the systemd path of the keeper's install")
class KeeperService(unittest.TestCase):
    def install(self, **env):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "fakebin").mkdir()
            for name in ("systemctl", "loginctl"):
                (home / "fakebin" / name).write_text("#!/bin/sh\nexit 0\n")
                (home / "fakebin" / name).chmod(0o755)
            full = {**{k: v for k, v in os.environ.items() if not k.startswith("BOT_")}, "HOME": tmp, "USER": "tester",
                    "PATH": f"{home / 'fakebin'}{os.pathsep}{os.environ['PATH']}", **env}
            r = subprocess.run([sys.executable, str(ROOT / "bin/keeper"), "install"], capture_output=True, text=True, env=full, timeout=30)
            log = home / ".config/bot_board/keeper.log"
            return r, log.read_text() if log.exists() else ""

    def test_alone_it_says_what_it_installed(self):
        r, log = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("keeper: installed and started", r.stdout)
        self.assertEqual(log, "")

    def test_under_bot_board_install_it_writes_the_same_to_the_log_and_prints_nothing(self):
        r, log = self.install(BOT_BOARD_QUIET="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("keeper: installed and started", log)
        self.assertIn("sudo loginctl enable-linger tester", log)

    def test_the_log_is_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "fakebin").mkdir()
            for name in ("systemctl", "loginctl"):
                (home / "fakebin" / name).write_text("#!/bin/sh\nexit 0\n"); (home / "fakebin" / name).chmod(0o755)
            env = {**{k: v for k, v in os.environ.items() if not k.startswith("BOT_")}, "HOME": tmp, "USER": "tester", "BOT_BOARD_QUIET": "1",
                   "PATH": f"{home / 'fakebin'}{os.pathsep}{os.environ['PATH']}"}
            subprocess.run([sys.executable, str(ROOT / "bin/keeper"), "install"], capture_output=True, text=True, env=env, timeout=30, check=True)
            self.assertEqual(oct((home / ".config/bot_board/keeper.log").stat().st_mode & 0o777), "0o600")

    def no_systemd(self, systemctl):
        """`keeper install` on a box with no systemd user manager: no systemctl at all, or one that cannot reach the user manager."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "fakebin").mkdir()
            path = str(home / "fakebin")
            if systemctl:
                (home / "fakebin" / "systemctl").write_text(systemctl); (home / "fakebin" / "systemctl").chmod(0o755)
            # a PATH with python and the basics but no systemctl
            env = {**{k: v for k, v in os.environ.items() if not k.startswith("BOT_")}, "HOME": tmp, "USER": "tester",
                   "PATH": path + os.pathsep + os.pathsep.join(d for d in os.environ["PATH"].split(os.pathsep) if not (Path(d) / "systemctl").exists())}
            out = []
            for cmd in ("install", "uninstall"):
                out.append(subprocess.run([sys.executable, str(ROOT / "bin/keeper"), cmd], capture_output=True, text=True, env=env, timeout=30))
            return out, home

    def test_without_systemd_the_install_says_so_in_one_line_and_exits_75_with_no_traceback(self):
        for systemctl in ("", "#!/bin/sh\necho 'Failed to connect to bus' >&2\nexit 1\n"):
            with self.subTest(systemctl=bool(systemctl)):
                (install, uninstall), home = self.no_systemd(systemctl)
                self.assertEqual(install.returncode, 75, install.stderr)
                self.assertNotIn("Traceback", install.stderr)
                self.assertIn("no systemd user manager is reachable", install.stderr)
                self.assertIn("enable-linger", install.stderr)
                self.assertIn("keeper run", install.stderr)
                self.assertIn("run bot_board install bots again", install.stderr)

    def test_the_message_is_printed_under_bot_board_install_too_because_a_person_must_act_on_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**{k: v for k, v in os.environ.items() if not k.startswith("BOT_")}, "HOME": tmp, "BOT_BOARD_QUIET": "1",
                   "PATH": os.pathsep.join(d for d in os.environ["PATH"].split(os.pathsep) if not (Path(d) / "systemctl").exists())}
            r = subprocess.run([sys.executable, str(ROOT / "bin/keeper"), "install"], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 75)
        self.assertIn("no systemd user manager is reachable", r.stderr)

    def test_without_systemd_the_uninstall_removes_the_unit_file_and_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            unit = Path(tmp) / ".config/systemd/user/bot-board-keeper.service"
            unit.parent.mkdir(parents=True); unit.write_text("[Unit]\n")
            env = {**{k: v for k, v in os.environ.items() if not k.startswith("BOT_")}, "HOME": tmp,
                   "PATH": os.pathsep.join(d for d in os.environ["PATH"].split(os.pathsep) if not (Path(d) / "systemctl").exists())}
            r = subprocess.run([sys.executable, str(ROOT / "bin/keeper"), "uninstall"], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertFalse(unit.exists())


if __name__ == "__main__":
    unittest.main()
