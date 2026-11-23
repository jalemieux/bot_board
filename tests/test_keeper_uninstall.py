"""#554: removing the keeper's service on a box with no systemd (a container) is not an error: the unit file is
removed and the command ends well. Run with python -m unittest discover -s tests."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform.startswith("linux"), "the systemd path is Linux's")
class KeeperUninstall(unittest.TestCase):
    def test_no_systemctl_still_removes_the_unit_and_ends_well(self):
        with tempfile.TemporaryDirectory() as home:
            unit = Path(home, ".config/systemd/user/bot-board-keeper.service")
            unit.parent.mkdir(parents=True)
            unit.write_text("[Unit]\n")
            empty = Path(home, "empty-path")
            empty.mkdir()
            r = subprocess.run([sys.executable, str(ROOT / "bin/keeper"), "uninstall"], capture_output=True, text=True, timeout=30,
                               env={"HOME": home, "PATH": str(empty), "BOT_CONFIG": str(Path(home, "config"))})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(unit.exists())
            self.assertIn("service removed", r.stdout)
            self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
