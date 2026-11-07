"""New bots get a first name, never a hex suffix (board thread 11602).
Run with python -m unittest discover -s tests."""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import db, fleet     # noqa: E402

SCRIPT = (ROOT / "bin" / "bot").read_text(encoding="utf-8")


def script_names() -> list[str]:
    return re.search(r"^BOT_NAMES=\$\{BOT_NAMES:-(.*)\}$", SCRIPT, re.M).group(1).split()


def pick_name_source() -> str:
    return re.search(r"^pick_name\(\) \{\n(.*?)^\}", SCRIPT, re.M | re.S).group(1)


class Names(unittest.TestCase):
    def test_the_board_and_the_launcher_draw_from_the_same_long_list(self):
        self.assertEqual(list(fleet.NAMES), script_names())
        self.assertGreater(len(fleet.NAMES), 300)
        self.assertEqual(len(set(fleet.NAMES)), len(fleet.NAMES), "no name twice")

    def test_every_name_is_a_valid_handle_nobody_else_uses(self):
        for n in fleet.NAMES:
            self.assertRegex(n, db.HANDLE_RE)
            self.assertNotIn(n, ("board", "routines", "keeper", "admin"))

    def test_the_launcher_skips_taken_names_and_never_falls_back_to_hex(self):
        src = pick_name_source()
        with tempfile.TemporaryDirectory() as cfg:
            def pick(names, *tried):
                out = subprocess.run(["bash", "-c", "CFG=$1; BOT_NAMES=$2; shift 2\npick_name() {\n" + src + "\n}\npick_name \"$@\"",
                                      "x", cfg, names, *tried], capture_output=True, text=True, check=True)
                return out.stdout.strip()
            Path(cfg, "ada.token").write_text("x")
            os.makedirs(os.path.join(cfg, "bots", "bea"))
            self.assertEqual(pick("ada bea cleo"), "cleo", "a name this box has had is not drawn again")
            self.assertEqual(pick("ada bea cleo dex", "cleo"), "dex", "nor one the board refused")
            for _ in range(20):      # all gone: a first name and a number
                self.assertRegex(pick("ada bea", "cleo", "dex"), r"^(ada|bea)\d{1,2}$")


if __name__ == "__main__":
    unittest.main()
