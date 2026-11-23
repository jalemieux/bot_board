"""The workspace switcher's menu is written once and both pages use it (#660). Run with python -m unittest discover -s tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import web, workspace_web  # noqa: E402


class SharedSwitcher(unittest.TestCase):
    def pages(self):
        board = web.app_page("t", "", [], [], [], {})
        ws = workspace_web.workspaces_page("t")
        return board, ws

    def test_both_pages_carry_the_shared_script_once(self):
        for page in self.pages():
            self.assertEqual(page.count("function wsSwitcher("), 1)
            self.assertEqual(page.count("function wsRows("), 1)

    def test_neither_page_builds_the_rows_or_the_listeners_itself(self):
        for page in self.pages():
            self.assertEqual(page.count('data-ws="'), 1, "the one place that writes a row")
            self.assertNotIn("BAR_BOUND", page)

    def test_the_rows_escape_what_they_show(self):
        self.assertIn("wsEsc(x.label || x.slug)", web.WS_SWITCHER_JS)
        self.assertIn("wsEsc(x.slug)", web.WS_SWITCHER_JS)


if __name__ == "__main__":
    unittest.main()
