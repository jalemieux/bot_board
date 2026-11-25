"""#890: the Mac's card (the host-bots switch, its host bots and the short form) is drawn by the Machines page's script from /api/fleet,
/api/workspaces and /api/profiles, as a full-width row under its machine. The script runs under node against a small stand-in document
that can say what it built. Skipped without node. Run with python -m unittest discover -s tests."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import web

DOM = r"""
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
class T { constructor(t) { this.text = t; } get textContent() { return this.text; } html() { return esc(this.text); } }
class N { constructor(t) { this.tag = t; this.children = []; this.attrs = []; this.parent = null; }
  setAttribute(k, v) { const a = this.attrs.find(x => x[0] === k); if (a) a[1] = v; else this.attrs.push([k, String(v)]); }
  getAttribute(k) { const a = this.attrs.find(x => x[0] === k); return a ? a[1] : null; }
  get id() { return this.getAttribute('id'); }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  insertBefore(c, ref) { c.parent = this; const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(c); else this.children.splice(i, 0, c); return c; }
  get nextSibling() { const i = this.parent.children.indexOf(this); return this.parent.children[i + 1] || null; }
  set textContent(v) { this.children = [new T(v)]; }
  get textContent() { return this.children.map(c => c.textContent).join(''); }
  get rows() { return this.children.filter(c => c.tag === 'tr'); }
  html() { return '<' + this.tag + this.attrs.map(a => ' ' + a[0] + '="' + esc(a[1]) + '"').join('') + '>' + this.children.map(c => c.html()).join('') + '</' + this.tag + '>'; } }
const tbody = new N('tbody'); tbody.tBodies = null;
const table = new N('table'); table.tBodies = [tbody];
%s.forEach(id => { const tr = new N('tr'); tr.setAttribute('id', id); tbody.appendChild(tr); });
global.document = {createElement: t => new N(t), createTextNode: t => new T(t), querySelector: s => s === '#fleet-machines table.machines' ? table : null};
global.localStorage = {getItem: () => null};
const DATA = %s;
global.fetch = u => Promise.resolve({ok: true, json: () => Promise.resolve(DATA[u.split('?')[0]] || {})});
"""
TAIL = "\nsetTimeout(() => console.log(JSON.stringify(tbody.children.map(r => r.html()))), 20);\n"


def run(rows, data):
    code = DOM % (json.dumps(rows), json.dumps(data)) + web.HOST_CARD_JS + TAIL
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-600:]
    return json.loads(r.stdout)


def mac(on=True, reporting=True, **extra):
    base = dict(name="jacs-m5", reporting=reporting, host_bots=on, host_allowed=["mira"], apps=[{"name": "Godot", "version": "4.6.1"}],
                bots=[{"handle": "mira", "host_flag": True}, {"handle": "edie", "host_flag": False}])
    return {**base, **extra}


API = {"/api/workspaces": {"workspaces": [{"slug": "ogen_game"}, {"slug": "old", "archived": True}]}, "/api/profiles": {"profiles": [{"name": "Mac runner"}]}}


class HostWarningEmbed(unittest.TestCase):
    def test_the_embedded_warning_has_no_angle_bracket_and_reads_back_as_the_text(self):      # #903
        line = web.HOST_CARD_JS.split("\n", 1)[0]
        self.assertTrue(line.startswith("var HOST_WARNING = ") and line.endswith(";"))
        embedded = line[len("var HOST_WARNING = "):-1]
        self.assertNotIn("<", embedded, "a </script> in the text could not end the page's script")
        self.assertEqual(json.loads(embedded), web.HOST_WARNING)


@unittest.skipUnless(shutil.which("node"), "node is needed")
class HostCard(unittest.TestCase):
    def rows(self, hosts, rows=("box-jacs-m5", "box-box")):
        return run(list(rows), {"/api/fleet": {"hosts": hosts}, **API})

    def test_a_linux_box_card_says_machine_not_mac(self):      # #877: the switch is on any operating system
        card = self.rows([mac(host_allowed=[], os="Linux 6.8", apps=[])])[1]
        self.assertIn("allowed on this machine: nobody yet", card)
        self.assertIn("none reported by this machine", card)
        self.assertNotIn("this Mac", card)
        self.assertNotIn("the Mac", card)
        self.assertNotIn("/Applications", card)

    def test_a_mac_gets_a_full_width_row_under_its_machine_and_a_linux_box_gets_none(self):
        out = self.rows([mac(), dict(name="box", reporting=True, host_bots=None, bots=[])])
        self.assertEqual(len(out), 3)
        self.assertTrue(out[0].startswith('<tr id="box-jacs-m5">') and out[2].startswith('<tr id="box-box">'))
        self.assertTrue(out[1].startswith('<tr class="bot-drawer hostrow"><td colspan="7"><div class="hostbox" data-host="jacs-m5">'), out[1])
        self.assertEqual(sum(r.count('class="hostbox"') for r in out), 1, "the Linux box has no host-bots block")

    def test_the_switch_the_host_bots_and_the_form_when_it_is_on(self):
        card = self.rows([mac(on=True)])[1]
        self.assertIn('class="act host-switch needs-me" data-host="jacs-m5" data-on="0"', card)      # on: the button turns it off
        self.assertIn('<a href="/a/mira">@mira</a>', card)
        self.assertNotIn("@edie", card, "only the bots that run as the user")
        self.assertIn("allowed on this machine: mira", card)
        self.assertIn("1 apps found</summary>Godot 4.6.1", card)      # the app list sits folded under its count
        self.assertIn('<form class="hostform" data-host="jacs-m5">', card)
        self.assertIn("It can read every bot", card)                       # the warning the form carries
        self.assertIn('name="ack" required=""', card)
        self.assertIn('<option value="ogen_game">ogen_game</option>', card)
        self.assertNotIn(">old<", card, "an archived workspace is not offered")
        self.assertIn('<option value="Mac runner">Mac runner</option>', card)
        self.assertIn('name="app" value="Godot"', card)

    def test_a_reader_who_is_not_given_the_consent_list_sees_unknown_not_nobody(self):
        # #927: host_allowed is for signed-in humans; a missing list is unknown, never "nobody yet"
        base = mac()
        base.pop("host_allowed")
        card = self.rows([base])[1]
        self.assertIn("allowed on this machine: shown to signed-in humans", card)
        self.assertNotIn("nobody yet", card)
        self.assertIn("allowed on this machine: nobody yet", self.rows([mac(host_allowed=[])])[1])

    def test_off_has_no_form_and_says_every_bot_runs_in_a_container(self):
        card = self.rows([mac(on=False, bots=[{"handle": "edie", "host_flag": False}])])[1]
        self.assertIn('data-on="1"', card)
        self.assertIn("off: every bot here runs in a container", card)
        self.assertNotIn('class="hostform"', card, "no form while the switch is off")
        self.assertNotIn("pip bad", card, "off is a normal state, not a warning")

    def test_a_mac_that_is_not_reporting_has_no_form_either(self):
        self.assertNotIn('class="hostform"', self.rows([mac(on=True, reporting=False)])[1])

    def test_values_are_text_and_a_machine_without_a_row_is_skipped(self):
        evil = mac(apps=[{"name": "<img src=x onerror=alert(1)>", "version": "1"}], host_allowed=["<b>x</b>"])
        card = self.rows([evil])[1]
        self.assertNotIn("<img", card)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt; 1", card)
        self.assertEqual(len(self.rows([mac()], rows=("box-other",))), 1, "no row for it on the page: nothing inserted")


if __name__ == "__main__":
    unittest.main()
