"""#874: the Skills page's script draws the list from /api/skills and /api/profiles, with every value as text. It runs under node against a
small stand-in document. Skipped without node. Run with python -m unittest discover -s tests."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import registry_web

HARNESS = r"""
class N { constructor(t) { this.tag = t; this.children = []; this.attrs = {}; this._t = ''; this.className = ''; this.classList = {add() {}}; }
  setAttribute(k, v) { this.attrs[k] = v; } appendChild(c) { this.children.push(c); return c; }
  set textContent(v) { this._t = v; this.children = []; } get textContent() { return this._t + this.children.map(c => c.textContent).join(''); }
  scrollIntoView() {} }
const els = {'skills-list': new N('div'), 'skills-count': new N('p'), 'skills-h': new N('h2')};
global.document = {createElement: t => new N(t), createTextNode: t => { const n = new N('#t'); n._t = t; return n; },
  getElementById: i => els[i] || null, querySelector: s => s.includes('data-page=skills') ? new N('div') : null};
global.localStorage = {getItem: () => null}; global.location = {hash: %s};
const SKILLS = %s, PROFILES = %s;
global.fetch = u => Promise.resolve({ok: SKILLS !== null, json: () => Promise.resolve(SKILLS === null ? {detail: 'refused'} : (u.includes('skills') ? {skills: SKILLS} : {profiles: PROFILES}))});
"""
SCRIPT_TAIL = "\nsetTimeout(() => { console.log(JSON.stringify({count: els['skills-count'].textContent, head: els['skills-h'].textContent, list: els['skills-list'].textContent, hrefs: (function w(n) { return [].concat(n.attrs.href ? [n.attrs.href] : [], ...n.children.map(w)); })(els['skills-list'])})); }, 20);\n"


def run(skills, profiles, hash=""):
    code = HARNESS % (json.dumps(hash), skills, profiles) + registry_web.SKILLS_SHELL_JS + SCRIPT_TAIL
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-500:]
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which("node"), "node is needed")
class SkillsShell(unittest.TestCase):
    SKILLS = ('[{name: "forgejo-lifecycle", description: "<b>x</b> a", revision: 9, updated_at: "2026-11-20T00:00:00+00:00", author: "jac", base: true, used_by: []},'
              ' {name: "art", description: "Art", revision: 2, updated_at: "2026-11-10T00:00:00+00:00", author: "jac", base: false, used_by: ["dev", "admin"]}]')
    PROFILES = '[{name: "dev", bots: ["a", "b"]}, {name: "admin", bots: []}]'

    def test_it_draws_base_and_profile_skills_with_their_counts_and_users(self):
        out = run(self.SKILLS, self.PROFILES)
        self.assertEqual((out["count"], out["head"]), ("2 skills", "Skills · 2"))
        self.assertIn("Base skills · every bot · 1", out["list"])
        self.assertIn("Profile skills · 1", out["list"])
        self.assertIn("rev 9", out["list"])
        self.assertIn("2 bots", out["list"], "the bots of the profiles that use it")
        self.assertIn("/fleet/skills/art#history", out["hrefs"])
        self.assertIn("/fleet/profiles/dev", out["hrefs"])

    def test_a_malformed_hash_is_no_target_and_ends_nothing(self):          # #892
        for hash in ("#%E0%A4%A", "#%", "#%zz"):
            out = run(self.SKILLS, self.PROFILES, hash)                      # run() fails on an uncaught error
            self.assertEqual(out["head"], "Skills \u00b7 2", hash)
            self.assertIn("Profile skills \u00b7 1", out["list"], hash)

    def test_a_good_hash_still_scrolls_to_its_row(self):
        self.assertEqual(run(self.SKILLS, self.PROFILES, "#base-skills")["head"], "Skills \u00b7 2")

    def test_a_description_is_text_not_markup(self):
        self.assertIn("<b>x</b> a", run(self.SKILLS, self.PROFILES)["list"])

    def test_none_and_a_failed_read_say_so(self):
        self.assertIn("No skill yet", run("[]", "[]")["list"])
        out = run("null", "[]")
        self.assertIn("Could not load the skills: refused", out["list"])
        self.assertEqual(out["count"], "")


if __name__ == "__main__":
    unittest.main()
