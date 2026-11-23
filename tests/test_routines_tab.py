"""#820: the Routines tab's script under node against a small fake DOM: a card per routine, the read-only box and its two plain
buttons for someone who cannot change a routine, the real actions for the admin owner, a waiting version with its diff marked,
a failure in words, and the empty state. Skipped without node."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import workspace_web
from test_activity_page import FAKE

ROUTINES = [
    {"id": 1, "name": "board-care", "state": "enabled", "about": "Looks after the board", "channel": "admin", "every": 3600, "version": 4, "thread": 77,
     "waiting": {"changed": "its check", "diff": "-old line\n+new line", "by": "ada"}, "jobs": [{"id": 90, "done_at": None}], "check": "echo hi", "prompt": "do it"},
    {"id": 2, "name": "issues", "state": "failed", "channel": "admin", "every": 60, "version": 1, "failure": "nobody took it", "notify": "jac", "jobs": [], "check": "x", "prompt": "y"},
]


class RoutinesTab(unittest.TestCase):
    def run_view(self, routines, can_manage, me):
        if not shutil.which("node"):
            self.skipTest("node is needed")
        js = workspace_web.WS_JS
        h = js[js.index("function h(tag, attrs)"):js.index("function api(")]
        view = js[js.index("// The Routines tab (#820"):js.index("// The Team tab (#645)")]
        script = FAKE.replace("var ME = null;", "var ME = %s;" % json.dumps(me)) + "global.window = {confirm: () => true};\nfunction note(el, t) { el.textContent = t; }\n" + h + view + """
const assert = require('node:assert/strict');
function api(method, path) {
  if (path.startsWith('/api/workspaces/admin')) return Promise.resolve({workspace: {can_manage: %s}});
  return Promise.resolve({routines: %s});
}
globalThis.location = {hash: ''};
routinesView({slug: 'general', name: 'General'});
setTimeout(() => { const sec = root.kids[0]; %s }, 20);
""" % (json.dumps(can_manage), json.dumps(routines), "%s")
        return script

    def go(self, body, routines=ROUTINES, can_manage=False, me=None):
        script = self.run_view(routines, can_manage, me) % body
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_someone_who_cannot_change_a_routine_reads_it_and_is_told_how_to_ask(self):
        self.go("""
          const t = sec.textContent;
          assert.ok(t.includes('You can read these.'));
          assert.equal(sec.all('article').length, 2);
          const hrefs = sec.all('a').map(a => a.attrs.href);
          assert.ok(hrefs.includes('/t/77'), 'asking for a change opens the routine request thread');
          assert.ok(hrefs.includes('/c/admin'));
          assert.deepEqual(sec.all('button').map(b => b.textContent), [], 'no greyed buttons');
          assert.ok(t.includes('enabled, a new version waits'));
          assert.ok(sec.all('span').some(x => x.attrs['class'] === 'st waits'), 'a waiting version is not the healthy green');
          assert.ok(t.includes('The admin workspace\\u2019s owner (@jac) has been told.'));
        """)

    def test_the_admin_owner_gets_the_real_actions_and_a_marked_diff(self):
        self.go("""
          const labels = sec.all('button').map(b => b.textContent);
          assert.ok(labels.includes('Approve') && labels.includes('Pause') && labels.includes('Run now') && labels.includes('Enable again'));
          assert.ok(!sec.textContent.includes('You can read these.'));
          const marks = sec.all('div').filter(d => ['add', 'del'].includes(d.attrs['class'])).map(d => d.attrs['class'] + ':' + d.textContent);
          assert.deepEqual(marks, ['del:-old line', 'add:+new line']);
        """, can_manage=True, me={"handle": "jac", "token": "t"})

    def test_a_workspace_with_no_routine_says_so_and_where_to_ask(self):
        self.go("""
          assert.ok(sec.textContent.includes('No routines in General.'));
          assert.ok(sec.all('a').some(a => a.attrs.href === '/c/admin'));
        """, routines=[])


if __name__ == "__main__":
    unittest.main()
