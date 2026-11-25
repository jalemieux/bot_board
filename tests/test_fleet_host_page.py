"""/fleet for host bots (#595): the banner, the badge, the opened row, the Mac's card and its form. Run with
python -m unittest discover -s tests. The page is rendered from data; the form's script is only syntax-checked (node)."""
import os
import shutil
import subprocess
import tempfile
import unittest

from app import db, web


def bot(handle, status="waiting", **extra):
    return dict(handle=handle, status=status, harness="claude", **extra)


def host(name, bots, os_name="Linux 6.8", **extra):
    return dict(name=name, bots=bots, reporting=True, reported_at=db.now(), keeper="keeper-" + name, cores=8, load=0.3,
                mem_total_mb=16000, mem_free_mb=12000, os=os_name, **extra)


MIRA = dict(host_flag=True, on_host=True, runmode="host", host_reason="Godot captures", host_by="op", host_at=db.now(),
            host_apps=["Godot", "Blender"], want="running", set_by="op", set_at=db.now(), pool=True)


def mac(bots, on=True, **extra):
    return host("jacs-m5", bots, os_name="Darwin 25.0", host_bots=on, host_allowed=["mira"],
                apps=[{"name": "Godot", "version": "4.6.1"}], **extra)


class HostBotsPage(unittest.TestCase):
    def render(self, hosts):
        return web.fleet_page("board", hosts, workspaces=("ogen_game",), profiles=[{"name": "Mac runner", "role": "x"}])

    def test_the_switch_and_the_form_are_on_a_mac_only(self):
        page = self.render([mac([], on=True), host("box", [])])
        self.assertEqual(page.count('class="hostbox"'), 1, "the Linux box has no host-bots block")
        self.assertIn('<tr class="bot-drawer hostrow"><td colspan="7"><div class="hostbox"', page, "its own full-width row under the Mac")
        self.assertIn('data-on="0"', page)                  # on: the button turns it off
        self.assertIn('class="hostform"', page)
        self.assertIn("It can read every bot", page)        # the warning the form carries
        self.assertIn('name="ack" required', page)
        off = self.render([mac([], on=False)])
        self.assertIn('data-on="1"', off)
        self.assertNotIn('class="hostform"', off, "no form while the switch is off")

    def test_a_page_with_no_host_bot_has_no_banner(self):
        page = self.render([host("box", [bot("zed", runmode="native")])])
        self.assertNotIn('class="hostbanner"', page)      # the banner itself is the script's (#887): tests/test_fleet_alerts.py
        self.assertNotIn("hostbadge", page.split("</style>")[-1])

    @unittest.skipUnless(shutil.which("node"), "node is needed to check the script")
    def test_the_board_page_script_is_valid_javascript_and_marks_host_bots(self):
        self.assertIn("hostMark(", web.APP_JS)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "app.js")
            with open(path, "w") as fh:
                fh.write(web.APP_JS)
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipUnless(shutil.which("node"), "node is needed to check the script")
    def test_the_page_script_is_valid_javascript(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fleet.js")
            with open(path, "w") as fh:
                fh.write(web.FLEET_JS)
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
