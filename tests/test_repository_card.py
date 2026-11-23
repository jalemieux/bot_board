"""#743: the Team tab's Repository card links an existing repository by name, through the same PUT the API has. The card's
script runs under node against a small fake DOM. Skipped without node."""
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
  insertBefore(c,ref){const i=this.kids.indexOf(ref);this.kids.splice(i<0?this.kids.length:i,0,c);return c;} get firstChild(){return this.kids[0];}
  addEventListener(e,f){this.handlers[e]=f;} get children(){return this.kids.filter(k=>k.tag);}
  querySelectorAll(s){const out=[];const walk=n=>{n.kids.forEach(k=>{if(k.tag==='button')out.push(k);walk(k);});};walk(this);return out;}
  all(tag){const out=[];const walk=n=>{n.kids.forEach(k=>{if(k.tag===tag)out.push(k);walk(k);});};walk(this);return out;}
  click(){this.handlers.click&&this.handlers.click();} }
global.Node = N;
global.document = {createElement:t=>new N(t), createTextNode:s=>{const n=new N('');n._t=s;return n;}};
var root = new N('div');
var ME = null;
"""


class RepositoryCard(unittest.TestCase):
    def test_a_manager_changes_or_unlinks_a_linked_repository(self):
        if not shutil.which("node"):
            self.skipTest("node is needed")
        js = workspace_web.WS_JS
        h = js[js.index("function h(tag, attrs)"):js.index("function api(")]
        note = js[js.index("function note("):js.index("function field(")]
        card = js[js.index("function repositoryCard("):js.index("function teamView(")]
        script = FAKE + h + note + card + """
const assert = require('node:assert/strict');
N.prototype.focus = function () { this.focused = true; };
const calls = []; global.location = {reload() { calls.push('reload'); }};
function api(method, path, body) { calls.push([method, path, body]); return Promise.resolve({}); }
ME = {token: 't'};
const w = {slug: 'game', can_manage: true, archived: false, repository: 'jac/old', repository_url: 'http://f/jac/old'};
const sec = repositoryCard(w);
assert.deepEqual(sec.all('a').map(a => a.attrs.href), ['http://f/jac/old']);
// Change: the same field, the same PUT
const input = sec.all('input')[0], link = sec.all('button').find(b => b.textContent === 'Link repository');
input.value = 'jac/new'; input.handlers.input(); link.click();
assert.deepEqual(calls[0], ['PUT', '/api/workspaces/game/repository', {repository: 'jac/new'}]);
// Unlink asks first, then sends an empty repository
const unlink = sec.all('button').find(b => b.textContent === 'Unlink');
const yes = sec.all('button').find(b => b.textContent === 'Unlink jac/old'), no = sec.all('button').find(b => b.textContent === 'Cancel');
assert.equal(calls.length, 1, 'nothing sent yet');
assert.equal(unlink.attrs['class'], 'quiet danger'); assert.equal(yes.attrs['class'], 'quiet danger');
unlink.click(); assert.equal(unlink.hidden, true); assert.equal(no.focused, true, 'the safe button has focus');
no.click(); assert.equal(unlink.hidden, false);
unlink.click(); yes.click();
assert.deepEqual(calls[1], ['PUT', '/api/workspaces/game/repository', {repository: ''}]);
setTimeout(() => {
  assert.ok(calls.includes('reload'));
  // not a manager: the link, a visible reason, and no controls
  const quiet = repositoryCard({slug: 'game', can_manage: false, repository: 'jac/old'});
  assert.equal(quiet.all('button').length, 0);
  assert.ok(quiet.textContent.includes('Only a manager of this workspace'));
}, 20);
"""
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_a_manager_links_an_existing_repository_and_sees_a_refusal(self):
        if not shutil.which("node"):
            self.skipTest("node is needed")
        js = workspace_web.WS_JS
        h = js[js.index("function h(tag, attrs)"):js.index("function api(")]
        note = js[js.index("function note("):js.index("function field(")]
        card = js[js.index("function repositoryCard("):js.index("function teamView(")]
        script = FAKE + h + note + card + """
const assert = require('node:assert/strict');
const calls = []; let refuse = null; global.location = {reload() { calls.push('reload'); }};
function api(method, path, body) { calls.push([method, path, body]); return refuse ? Promise.reject(new Error(refuse)) : Promise.resolve({}); }
ME = {token: 't'};
const w = {slug: 'game', name: 'Game', can_manage: true, archived: false, repository: ''};
const sec = repositoryCard(w);
const input = sec.all('input')[0], link = sec.all('button').find(b => b.textContent === 'Link repository');
assert.equal(link.attrs.disabled, '', 'nothing typed: nothing to link');
for (const bad of ['jac', 'jac/', '/x', 'a b/c', 'a/b/c']) { input.value = bad; input.handlers.input(); assert.equal(link.disabled, true, bad); }
input.value = ' jac/bot_board '; input.handlers.input(); assert.equal(link.disabled, false);
link.click();
assert.deepEqual(calls[0], ['PUT', '/api/workspaces/game/repository', {repository: 'jac/bot_board'}]);
setTimeout(() => {
  assert.equal(calls[1], 'reload', 'a link reloads the card');
  refuse = 'jac/bot_board belongs to the workspace other.'; calls.length = 0;
  input.handlers.input(); link.click();
  setTimeout(() => {
    assert.ok(sec.textContent.includes('belongs to the workspace other.'), 'the route\\'s refusal is shown');
    assert.equal(link.disabled, false, 'and the button is usable again');
    const linkOut = sec.all('p').find(p => p.textContent.includes('belongs to the workspace'));
    assert.equal(linkOut.attrs.role, 'alert', 'a refusal is read at once');
    assert.ok(sec.all('p').some(p => p.attrs['class'] === 'linkrow'), 'the field and the button share a row');
    // not a manager: nothing to press
    const quiet = repositoryCard({slug: 'game', can_manage: false, repository: ''});
    assert.equal(quiet.all('input')[0].attrs.disabled, '');
    assert.ok(quiet.textContent.includes('Only a manager of this workspace'), 'a visible reason, not only a tooltip');
    // a linked workspace has no form
    assert.equal(repositoryCard({slug: 'game', repository: 'jac/x', repository_url: 'http://f/jac/x'}).all('input').length, 0);
}, 20);
}, 20);
"""
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
