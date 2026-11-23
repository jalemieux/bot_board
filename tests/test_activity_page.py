"""#738: the Activity tab's script, run under node against a small fake DOM: the counts and rows come from the data, a
count narrows the feed, forge text stays text, and a javascript: address makes no link. Skipped without node."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import workspace_web

FAKE = """
class N { constructor(t){this.tag=t;this.attrs={};this.kids=[];this.handlers={};this._t='';}
  setAttribute(k,v){this.attrs[k]=v;} appendChild(c){this.kids.push(c);return c;}
  set textContent(v){this._t=v;if(v==='')this.kids=[];} get textContent(){return this._t+this.kids.map(k=>k.textContent).join('');}
  addEventListener(e,f){this.handlers[e]=f;} get children(){return this.kids.filter(k=>k.tag);}
  querySelectorAll(s){const out=[];const walk=n=>{n.kids.forEach(k=>{if(k.tag==='button')out.push(k);walk(k);});};walk(this);return out;}
  all(tag){const out=[];const walk=n=>{n.kids.forEach(k=>{if(k.tag===tag)out.push(k);walk(k);});};walk(this);return out;}
  click(){this.handlers.click&&this.handlers.click();} }
global.Node = N;
global.document = {createElement:t=>new N(t), createTextNode:s=>{const n=new N('');n._t=s;return n;}};
var root = new N('div');
var ME = null;
"""


class ActivityPage(unittest.TestCase):
    def test_rows_counts_filter_and_unsafe_links(self):
        if not shutil.which("node"):
            self.skipTest("node is needed")
        js = workspace_web.WS_JS
        h = js[js.index("function h(tag, attrs)"):js.index("function api(")]
        act = js[js.index("var ACT_STATS"):js.index("// The Team tab (#645)")]
        data = {"activity": {"since": "2026-11-23T00:00:00Z", "hours": 24, "repository": "jac/x", "forge": "ok", "reason": "", "capped": True,
                             "counts": {"merged": 1, "opened": 1, "closed": 0, "review": 0, "commit": 0, "thread": 0, "message": 3, "bots": 1},
                             "events": [{"at": "2026-11-23T10:00:00Z", "kind": "merged", "text": "PR #7 merged: <b>x</b>", "url": "javascript:alert(1)", "by": "ken"},
                                        {"at": "2026-11-23T09:00:00Z", "kind": "opened", "text": "PR #7 opened", "url": "http://f/pulls/7", "by": ""}]}}
        script = FAKE + h + act + """
const assert = require('node:assert/strict');
function api() { return Promise.resolve(%s); }
activityView({slug: 'general', name: 'General'});
setTimeout(() => {
  const sec = root.kids[0];
  const links = sec.all('a');
  assert.deepEqual(links.map(a => a.attrs.href), ['http://f/pulls/7'], 'only the http address is a link');
  assert.ok(sec.textContent.includes('PR #7 merged: <b>x</b>'), 'forge text stays text');
  assert.ok(sec.textContent.includes('author not named'));
  assert.ok(sec.textContent.includes('the counts are at least these'));
  assert.equal(sec.all('li').length, 2);
  const merged = sec.all('button').find(b => b.textContent.includes('PRs merged'));
  merged.click();
  assert.equal(sec.all('li').length, 1, 'a count narrows the feed');
  assert.equal(merged.attrs['aria-pressed'], 'true');
  merged.click();
  assert.equal(sec.all('li').length, 2);
}, 20);
""" % json.dumps(data)
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
