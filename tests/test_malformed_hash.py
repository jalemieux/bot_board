"""#909 (#892): a malformed #hash such as #%E0%A4%A is no target, not an error that ends the page's script. decodeURIComponent throws on it.
The fleet pages' script runs to its end under node with a bad hash; the workspace page's hash helper returns '' for one; and no script
decodes location.hash or location.pathname outside a try. #920: a skill's or a profile's page with a malformed percent in its path says
there is no such name, not "Loading". Skipped without node. Run with python -m unittest discover -s tests."""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import db, web

BAD = ("#%E0%A4%A", "#%", "#%zz")
STUBS = """
const noop = () => {};
const classList = {add: noop, remove: noop, toggle: (c, on) => { if (c === 'on') globalThis.TOGGLED = (globalThis.TOGGLED || 0) + (on ? 1 : 0); }, contains: () => false};
const el = new Proxy({classList, dataset: {panes: 'tab-settings'}, style: {}, children: [], id: 'x'}, {get(t, k) {
  if (k in t) return t[k];
  if (k === 'querySelectorAll') return () => [];
  if (k === 'querySelector' || k === 'closest' || k === 'parentElement') return () => null;
  return typeof k === 'string' && /^(add|remove|set|append|insert|focus|scroll|click|select|prepend|replace)/.test(k) ? noop : undefined; }, set(t, k, v) { t[k] = v; return true; }});
const pane = {id: 'tab-settings', classList};
globalThis.document = {getElementById: id => IDS.has(id) ? el : null, createElement: () => el, body: el, activeElement: null, addEventListener: noop,
  querySelector: s => s === '.fleet-page' ? el : null, querySelectorAll: s => s === '.tab-pane' ? [pane] : [], documentElement: el};
globalThis.window = {addEventListener: noop, matchMedia: () => ({matches: false, addEventListener: noop})};
globalThis.location = {hash: %s, replace: noop, href: '', reload: noop, pathname: '/machines'};
const store = {getItem: () => null, setItem: noop, removeItem: noop};
globalThis.localStorage = store; globalThis.sessionStorage = store;
globalThis.setInterval = noop; globalThis.setTimeout = noop;
globalThis.fetch = () => new Promise(noop);
globalThis.navigator = {};
"""


def host(name):
    return dict(name=name, bots=[], reporting=True, reported_at=db.now(), keeper="k", cores=8, load=0.3, mem_total_mb=16000, mem_free_mb=12000, os="Linux")


@unittest.skipUnless(shutil.which("node"), "node is needed")
class MalformedHash(unittest.TestCase):
    def test_the_fleet_pages_script_still_shows_its_pane_on_a_bad_hash(self):
        for page in ("machines", "roles", "skills", "configuration"):
            html = web.fleet_page("b", [host("m")], page=page)
            script = max(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S), key=len)
            ids = json.dumps(sorted(set(re.findall(r'\bid="([^"]+)"', html))))
            for bad in BAD:
                with self.subTest(page=page, hash=bad):
                    code = f"const IDS = new Set({ids});\n" + STUBS % json.dumps(bad) + "\n" + script + "\nif (!globalThis.TOGGLED) throw new Error('showTarget never ran');\n"
                    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
                    self.assertEqual(r.returncode, 0, r.stderr[-400:])

    def test_the_workspace_pages_hash_helper_returns_nothing_for_a_bad_hash_and_the_text_for_a_good_one(self):
        src = (ROOT / "app/workspace_web.py").read_text()
        helper = re.search(r"function hashText\(\) \{.*?\n", src).group(0)
        for hash_, want in (("#doc=INDEX.md@2", "#doc=INDEX.md@2"), ("#doc=a%20b.md", "#doc=a b.md"), *[(b, "") for b in BAD]):
            with self.subTest(hash=hash_):
                r = subprocess.run(["node", "-e", f"globalThis.location = {{hash: {json.dumps(hash_)}}};\n{helper}\nconsole.log(JSON.stringify(hashText()));"],
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(json.loads(r.stdout), want, r.stderr[-300:])

    def test_no_script_decodes_the_hash_or_the_path_outside_a_try(self):
        for path in (ROOT / "app").glob("*.py"):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                for part in ("hash", "pathname"):
                    if f"decodeURIComponent(location.{part}" in line:
                        self.assertRegex(line, r"try \{[^}]*decodeURIComponent\(location\." + part, f"{path.name}:{n} decodes location.{part} outside a try")

    def test_a_skill_or_profile_page_with_a_malformed_path_says_there_is_no_such_name(self):
        from app import registry_web
        for js, what in ((registry_web.SKILL_PAGE_JS, "skill"), (registry_web.PROFILE_PAGE_JS, "profile")):
            for path in ("/fleet/%s/%%E0%%A4%%A" % ("skills" if what == "skill" else "profiles"), "/fleet/skills/%", "/fleet/skills/%zz"):
                with self.subTest(page=what, path=path):
                    code = ("global.localStorage = {getItem: () => null};\n"
                            "const root = {innerHTML: ''};\n"
                            "global.document = {body: {classList: {add() {}}}, getElementById: id => id === 'reg' ? root : null};\n"
                            "global.location = {pathname: %s, search: ''};\n"
                            "global.fetch = () => { throw new Error('a malformed name must not be fetched'); };\n"
                            % json.dumps(path) + js + "\nprocess.stdout.write(root.innerHTML);")
                    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
                    self.assertEqual(r.returncode, 0, r.stderr[-400:])
                    self.assertIn("no %s with that name" % what, r.stdout)

    def test_a_good_path_still_loads_the_page(self):
        from app import registry_web
        code = ("global.localStorage = {getItem: () => null};\n"
                "const root = {innerHTML: ''}; const urls = [];\n"
                "global.document = {body: {classList: {add() {}}}, getElementById: id => id === 'reg' ? root : null};\n"
                "global.location = {pathname: '/fleet/skills/art%20to%20mesh', search: ''};\n"
                "global.fetch = u => { urls.push(u); return new Promise(() => {}); };\n"
                + registry_web.SKILL_PAGE_JS + "\nprocess.stdout.write(JSON.stringify(urls));")
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("/api/skills/art%20to%20mesh", json.loads(r.stdout)[0])


if __name__ == "__main__":
    unittest.main()
