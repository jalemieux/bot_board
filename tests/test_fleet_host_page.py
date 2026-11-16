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

    def test_the_banner_the_badge_and_the_opened_row_say_what_a_host_bot_is(self):
        page = self.render([mac([bot("mira", **MIRA), bot("edie", runmode="native")]), host("box", [bot("zed", runmode="native")])])
        self.assertIn('class="hostbanner"', page)
        self.assertIn("1 bot runs outside a container", page)
        self.assertIn("Godot captures", page)
        self.assertEqual(page.count('class="hostbadge"'), 1, "only the host bot is marked, not a bot on a box with no runtime")
        self.assertIn("host · as op", page)
        self.assertIn("on its Mac, outside a container", page)
        self.assertIn("Runs on the host", page)
        self.assertIn("not found in /Applications on this Mac", page)       # Blender is not on this Mac
        self.assertIn("Godot <b>4.6.1</b>", page)
        self.assertIn("listed in the Mac&#x27;s host-bots-allowed file <b>yes</b>", page.replace("\\'", "&#x27;").replace("'", "&#x27;"))
        self.assertIn("Move back to a container", page)

    def test_a_host_bot_not_yet_listed_is_said_to_wait_for_the_owner(self):
        page = self.render([mac([bot("mira", **MIRA)], )])
        page2 = self.render([host("jacs-m5", [bot("mira", **MIRA)], os_name="Darwin 25.0", host_bots=True, host_allowed=[], apps=[])])
        self.assertNotIn("waiting for you", page)
        self.assertIn("waiting for you: add the line <b>mira</b>", page2)

    def test_a_host_bot_that_has_not_started_yet_is_said_to_wait_and_then_to_start_at_its_next_start(self):
        fresh = dict(MIRA, runmode="")
        waiting = self.render([host("jacs-m5", [bot("mira", **fresh)], os_name="Darwin 25.0", host_bots=True, host_allowed=[], apps=[])])
        self.assertIn("host · waiting for the Mac", waiting)
        self.assertIn("in a container now, and on its Mac from its next start", waiting)
        listed = self.render([mac([bot("mira", **fresh)])])
        self.assertIn("host · at its next start", listed)

    def test_a_flagged_bot_off_the_host_says_it_is_in_a_container(self):
        off = dict(MIRA, on_host=False)
        page = self.render([mac([bot("mira", **off)], on=False)])
        self.assertIn("host bot · in a container", page)
        self.assertIn("in a container (its Mac", page)

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

    def test_a_bot_moved_back_but_still_running_on_the_host_is_still_marked(self):
        gone = dict(want="running", pool=True, runmode="host", host_flag=False, on_host=False)
        page = self.render([mac([bot("mira", **gone)])])
        self.assertIn("host · leaving at its next restart", page)
        self.assertIn('class="hostbanner"', page)

    def test_a_page_with_no_host_bot_has_no_banner(self):
        page = self.render([host("box", [bot("zed", runmode="native")])])
        self.assertNotIn('class="hostbanner"', page)
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
