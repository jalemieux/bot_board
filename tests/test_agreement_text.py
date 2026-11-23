"""#679: the team agreement and its proposals carry no invisible character, checked on the server for every writer, and the
set of characters covers variation selectors, the combining grapheme joiner, the Hangul fillers and the blank braille pattern.
Run with python -m unittest discover -s tests."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import workspaces

HIDDEN = ["\u00ad", "\u034f", "\u061c", "\u115f", "\u1160", "\u180e", "\u200b", "\u200d", "\u202e", "\u2060", "\u2800",
          "\u3164", "\ufe00", "\ufe0f", "\ufeff", "\uffa0", "\U000e0001", "\U000e0100", "\U000e01ef"]
VISIBLE = ["plain text", "## Roles\n\n| Role | Profile |\n|---|---|", "naïve café – “quotes” …", "日本語 and العربية", "a\u00a0b"]


class AgreementText(unittest.TestCase):
    def test_each_invisible_character_is_refused_for_the_agreement_and_proposals(self):
        for c in HIDDEN:
            for path in ("team/agreement.md", "team/proposals/12.md", "Team/Agreement.md"):
                with self.assertRaises(ValueError, msg=(hex(ord(c)), path)) as ctx:
                    workspaces._check_agreement_text(path, "one line" + c + "another")
                self.assertIn("U+%04X" % ord(c), str(ctx.exception))

    def test_visible_text_and_other_documents_pass(self):
        for t in VISIBLE:
            workspaces._check_agreement_text("team/agreement.md", t)
        workspaces._check_agreement_text("notes/anything.md", "a\u200bb")      # only the agreement and its proposals

    def test_the_page_and_the_server_spell_the_same_set(self):
        js = (ROOT / "app/workspace_web.py").read_text()
        spelled = re.search(r"var HIDDEN = /(.+)/gu;", js).group(1)
        # turn the page's expression into Python's: \uXXXX stays, \u{XXXXX} becomes \UXXXXXXXX
        py = re.sub(r"\\u\{([0-9A-Fa-f]+)\}", lambda m: "\\U" + m.group(1).zfill(8), spelled)
        rx = re.compile(py)
        for c in HIDDEN:
            self.assertTrue(rx.search(c), "the page misses U+%04X" % ord(c))
        for t in VISIBLE:
            self.assertFalse(rx.search(t), t)


if __name__ == "__main__":
    unittest.main()
