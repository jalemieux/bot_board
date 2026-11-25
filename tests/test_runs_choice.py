"""#877: the Team tab's choice of where a bot runs (docs/runtime-choice.md). runsPart, the component the Add a bot dialog and a bot's
"where it runs" dialog share, runs under node against a small stand-in document, on each state of a machine the API treats differently.
The words are the UI designer's (board thread 20957). Skipped without node. Run with python -m unittest discover -s tests."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import workspace_web

DOM = r"""
class El {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.listeners = {}; this.hidden = false; this.checked = false; this.value = ''; this.disabled = false; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'value') this.value = String(v); if (k === 'hidden') this.hidden = true; }
  appendChild(c) { this.children.push(c); return c; }
  set textContent(t) { this.children = t === '' ? [] : [String(t)]; }
  get textContent() { return this.children.map(c => typeof c === 'string' ? c : c.textContent).join(''); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  fire(t) { (this.listeners[t] || []).forEach(f => f()); }
  all(tag) { return this.children.flatMap(c => typeof c === 'string' ? [] : [c, ...c.all(tag)]).filter(x => !tag || x.tag === tag); }
  querySelectorAll(sel) { return this.all(sel); }
}
global.Node = El;
global.document = {createElement: t => new El(t), createTextNode: t => String(t)};
"""
# h() and runsPart() as the page has them
SRC = workspace_web.WS_JS
CODE = SRC[SRC.index("function h(tag, attrs) {"):SRC.index("function api(method, path, body)")] + SRC[SRC.index("// Where a bot runs (#877"):SRC.index("// A host bot's block (#733")]


def run(script):
    r = subprocess.run(["node", "-e", DOM + CODE + "\nconst assert = require('node:assert/strict');\nconst out = [];\n" + script + "\nconsole.log(JSON.stringify(out));"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-900:]
    return json.loads(r.stdout)


BOXES = r"""
const apps = [{name: 'Godot', version: '4.6.1'}, {name: 'Blender', version: '4.2'}];
const BOX = {
  off:  {name: 'minipc-1', reporting: true, host_bots: false, runtime: {setting: 'docker'}, os: 'Linux 6.8', apps: []},
  on:   {name: 'jacs-m5', reporting: true, host_bots: true, runtime: {setting: 'docker'}, os: 'Darwin 25.0', apps, host_allowed: ['mira']},
  dark: {name: 'mac-old', reporting: false, host_bots: true, runtime: {setting: 'docker'}, os: 'Darwin 24.0', apps: []},
  bare: {name: 'pi-4', reporting: true, host_bots: null, runtime: {setting: 'host'}, os: 'Linux 6.8', apps: []},
};
const mk = (box, o) => runsPart(Object.assign({handle: '', now: 'container', box: () => BOX[box], harness: () => 'codex'}, o || {}));
const host = p => p.node.all('input').find(i => i.value === 'host');
const choose = (p, v) => { const i = p.node.all('input').find(x => x.value === v); i.checked = true; i.fire('change'); };
const text = p => p.node.textContent;
"""


@unittest.skipUnless(shutil.which("node"), "node is needed")
class RunsChoice(unittest.TestCase):
    def test_a_machine_with_no_container_runtime_shows_no_choice_and_sends_nothing(self):
        out = run(BOXES + "const p = mk('bare'); out.push(p.node.all('fieldset')[0].hidden, text(p), p.values(), p.ready(), p.changed());")
        self.assertEqual(out[0], True, "no Runs choice at all")
        self.assertIn("Every bot on pi-4 runs on the machine, because pi-4 has no container runtime. There is nothing to choose.", out[1])
        self.assertEqual(out[2:], [{}, True, False])

    def test_host_bots_off_says_who_turns_them_on_and_start_waits(self):
        out = run(BOXES + "const p = mk('off'); choose(p, 'host'); out.push(text(p), p.ready(), p.values());")
        self.assertIn("Host bots are off on minipc-1. A human turns them on, on the Machines page.", out[0])
        self.assertIn("Waiting: Host bots are off on minipc-1.", out[0])
        self.assertFalse(out[1])

    def test_a_machine_that_is_not_reporting_says_its_switch_and_apps_are_not_known(self):
        out = run(BOXES + "const p = mk('dark'); choose(p, 'host'); out.push(text(p), p.ready());")
        self.assertIn("mac-old is not reporting, so its host-bots switch and its apps are not known.", out[0])
        self.assertFalse(out[1])

    def test_a_machine_with_the_switch_on_needs_a_reason_and_sends_the_apps_ticked(self):
        out = run(BOXES + """const p = mk('on'); out.push(p.values(), p.ready(), p.changed()); choose(p, 'host');
          out.push(p.ready(), text(p).includes('Host bots are on.'), text(p).includes('Godot 4.6.1'), text(p).includes('After you start it, jacs-m5 must also allow this bot'));
          const why = p.node.all('input').find(i => i.attrs.type === 'text'); why.value = '  Godot captures  '; why.fire('input');
          p.node.all('input').find(i => i.value === 'Godot').checked = true;
          out.push(p.ready(), p.values(), p.changed(), text(p).includes('18 / 200'));""")
        self.assertEqual(out[0], {"runs": "container"})
        self.assertEqual(out[1:3], [True, False], "a container is the default and needs nothing")
        self.assertEqual(out[3:7], [False, True, True, True], "ready only with a reason; the machine says the consent line comes after the bot exists")
        self.assertEqual(out[7], True)
        self.assertEqual(out[8], {"runs": "host", "host_reason": "Godot captures", "host_apps": ["Godot"]})
        self.assertEqual(out[9:], [True, True])

    def test_a_linux_box_is_not_told_about_a_mac_or_applications(self):
        out = run(BOXES + """BOX.off.host_bots = true; const l = mk('off'); choose(l, 'host'); out.push(text(l));
          BOX.on.apps = []; const m = mk('on'); choose(m, 'host'); out.push(text(m));""")
        self.assertIn("none reported by this machine", out[0])
        self.assertNotIn("/Applications", out[0])
        self.assertNotIn("Mac", out[0])
        self.assertIn("none found in /Applications", out[1], "a Mac keeps its own words")

    def test_a_reason_is_capped_at_200_characters_in_the_field(self):
        out = run(BOXES + "const p = mk('on'); out.push(p.node.all('input').find(i => i.attrs.type === 'text').attrs.maxlength);")
        self.assertEqual(out, ["200"])

    def test_an_existing_bot_says_whether_the_machine_lists_it(self):
        script = BOXES + """for (const h of ['mira', 'bryn']) { const p = mk('on', {handle: h, now: 'host'}); out.push(text(p)); }
          BOX.on.host_allowed = undefined; out.push(text(mk('on', {handle: 'bryn', now: 'host'})));"""
        listed, missing, hidden = run(script)
        self.assertIn("jacs-m5 lists @mira.", listed)
        self.assertIn("Add the line bryn to ~/.config/bot_board/host-bots-allowed on jacs-m5. Until then the bot runs in a container.", missing)
        self.assertIn("whether jacs-m5 lists @bryn: shown to signed-in humans.", hidden)

    def test_a_change_is_only_a_change_when_the_choice_differs_from_now(self):
        out = run(BOXES + """const p = mk('on', {handle: 'bryn', now: 'host'}); out.push(p.changed()); choose(p, 'container'); out.push(p.changed(), p.ready(), p.values());""")
        self.assertEqual(out, [False, True, True, {"runs": "container"}])

    def test_the_form_hears_every_change(self):
        out = run(BOXES + "let n = 0; const p = mk('on', {onchange: () => n++}); const before = n; choose(p, 'host'); out.push(n > before);")
        self.assertEqual(out, [True])

    def test_a_claude_bot_in_a_container_on_a_mac_is_told_about_the_oauth_secret(self):
        out = run(BOXES + """const a = mk('on', {harness: () => 'claude'}); out.push(text(a).includes('CLAUDE_CODE_OAUTH_TOKEN'));
          out.push(text(mk('on')).includes('CLAUDE_CODE_OAUTH_TOKEN'), text(mk('off', {harness: () => 'claude'})).includes('CLAUDE_CODE_OAUTH_TOKEN'));""")
        self.assertEqual(out, [True, False, False], "only claude, only on a Mac, only in a container")

    def test_values_are_text_not_markup(self):
        out = run(BOXES + "BOX.on.apps = [{name: '<img src=x onerror=alert(1)>', version: '1'}]; const p = mk('on'); choose(p, 'host'); out.push(p.node.all('input').some(i => i.value === '<img src=x onerror=alert(1)>'));")
        self.assertEqual(out, [True], "set as a value and as text, never parsed")


class RunsDialogs(unittest.TestCase):
    JS = workspace_web.WS_JS

    def test_the_add_dialog_sends_the_choice_and_waits_on_it(self):
        add = self.JS[self.JS.index("add: function (role)"):self.JS.index("// Pause or resume the whole team")]
        for needle in ("runsPart({handle: ''", "Object.assign({host: host.value, harness: harness.value, workspace: w.slug, profile: name}, runs.values())",
                       "runs.ready()", "host.addEventListener('change', function () { runs.update(); })"):
            self.assertIn(needle, add)

    def test_a_bots_card_has_one_where_it_runs_dialog_that_warns_first_and_names_the_change(self):
        card = self.JS[self.JS.index("act('where it runs…'"):self.JS.index("act('role…'")]
        for needle in ("starts a new harness session and rereads its rules. Its checkout stays", "The keeper applies the change when the bot is idle",
                       "'Change where it runs'", "part.changed() && part.ready()", "run: want", "part.values()"):
            self.assertIn(needle, card)
        self.assertNotIn("move back to a container", self.JS, "one pattern for both directions")
        self.assertIn("edit why and apps…", self.JS, "a host bot keeps editing its reason and apps")


if __name__ == "__main__":
    unittest.main()
