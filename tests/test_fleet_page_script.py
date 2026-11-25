"""#807: the script of the Machines, Roles, Skills and Configuration pages must run to its end on a page that lacks the optional
elements (the bots page's `who` and `bot-group` went with it). If it throws before showTarget(), every pane keeps display:none and
the page is blank. The script runs under node against a document that has none of those elements. Skipped without node."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main

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
globalThis.location = {hash: '', replace: noop, href: '', reload: noop};
const store = {getItem: () => null, setItem: noop, removeItem: noop};
globalThis.localStorage = store; globalThis.sessionStorage = store;
globalThis.setInterval = noop; globalThis.setTimeout = noop;
globalThis.fetch = u => { (globalThis.FETCHED = globalThis.FETCHED || []).push(String(u)); return new Promise(noop); };
globalThis.navigator = {};
"""


class FleetPageScript(unittest.TestCase):
    def test_the_script_runs_to_its_end_and_shows_its_pane(self):
        if not shutil.which("node"):
            self.skipTest("node is needed")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db.DB_PATH = os.path.join(tmp.name, "board.db")
        db._local = threading.local()
        with TestClient(main.app) as client:
            tok = client.post("/api/agents", json={"handle": "jac", "kind": "human"}).json()["token"]
            for path in ("/configuration", "/machines", "/roles", "/skills"):
                html = client.get(path, headers={"Authorization": "Bearer " + tok}).text
                scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
                script = max(scripts, key=len)
                ids = json.dumps(sorted(set(re.findall(r'\bid="([^"]+)"', html))))
                code = f"const IDS = new Set({ids});\n" + STUBS + "\n" + script + "\nif (!globalThis.TOGGLED) throw new Error('showTarget never ran');\n"
                code += "if (%s !== (globalThis.FETCHED || []).includes('/api/status')) throw new Error('Versions are read from /api/status on the Machines page only (#888): ' + globalThis.FETCHED);\n" % json.dumps(path == "/machines").lower()
                r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0, f"{path}: {r.stderr[-600:]}")
        if hasattr(db._local, "conn"):
            db._local.conn.close()


if __name__ == "__main__":
    unittest.main()
