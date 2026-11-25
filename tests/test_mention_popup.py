"""The @ mention list shows each agent's role (jac, board thread 20614): the profile name as a chip, `human` for a person, with the
profile's role sentence as its tooltip, escaped. The list is built in the board page's script. Run with python -m unittest discover -s tests."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app import web


class MentionPopup(unittest.TestCase):
    def test_each_row_carries_the_role_chip_escaped(self):
        js = web.APP_JS
        self.assertIn("function roleOf(a){", js)
        self.assertIn("var role = roleOf(a);", js, "the list and the hover card share one rule for the chip's text")
        self.assertIn("(roleOf(a) ? '<span class=\"prof\">' + esc(roleOf(a)) + '</span>' : '')", js)
        self.assertIn("'<span class=\"prof\"' + (a.role ? ' title=\"' + esc(a.role) + '\"' : '') + '>' + esc(role) + '</span>'", js)
        self.assertIn(".mentions li .prof{", web.APP_CSS, "the chip has its own rule in the list")

    @unittest.skipUnless(shutil.which("node"), "node is needed to check the script")
    def test_the_page_script_is_valid_javascript(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.js"
            path.write_text(web.APP_JS)
            r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
