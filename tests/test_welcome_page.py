"""#775: the first-run guide. The redirect rule (pending sends `/` and the Admin General to /welcome; skipped, done or no
key does not), the page is served in any state, and its script run under node against a small fake DOM: the steps follow
the admin bot's state, forge text stays text, Skip and Send save the state and go to General. Run with
python -m unittest discover -s tests."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, guide, main, welcome_web

NODE_DOM = """
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


class WelcomeRoutes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        for name, value in (('DB_PATH', os.path.join(tmp.name, 'board.db')), ('_local', threading.local())):
            p = patch.object(db, name, value); p.start(); self.addCleanup(p.stop)
        self.client = TestClient(main.app); self.client.__enter__()
        self.addCleanup(lambda: hasattr(db._local, 'conn') and db._local.conn.close())
        self.addCleanup(self.client.__exit__, None, None, None)
        r = self.client.post('/api/agents', json={'handle': 'sam', 'kind': 'human'})
        self.auth = {'Authorization': 'Bearer ' + r.json()['token']}

    def get(self, path):
        return self.client.get(path, follow_redirects=False)

    def test_a_pending_guide_sends_the_front_pages_to_welcome(self):
        self.assertEqual(guide.state('sam'), 'pending', 'a fresh board starts it at the first sign-in')
        for path in ('/', '/c/admin'):
            r = self.get(path)
            self.assertEqual((r.status_code, r.headers['location']), (302, '/welcome'), path)
        self.assertEqual(self.get('/workspaces').status_code, 200, 'other pages are not touched')
        self.assertEqual(self.get('/c/nowhere').status_code, 404)

    def test_skipped_done_or_no_key_shows_the_board_as_before(self):
        for state in ('skipped', 'done'):
            self.client.put('/api/first-run', headers=self.auth, json={'state': state})
            self.assertEqual((self.get('/').status_code, self.get('/c/admin').status_code), (200, 200), state)
        c = db.conn(); c.execute("DELETE FROM registry_meta WHERE key LIKE 'first_run:%'"); c.commit()       # a board from before the guide
        self.assertEqual(self.get('/').status_code, 200)

    def test_welcome_opens_in_any_state_and_the_footer_links_it(self):
        self.client.put('/api/first-run', headers=self.auth, json={'state': 'done'})
        r = self.get('/welcome')
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="fr"', r.text)
        self.assertIn('<a href="/welcome">Setup guide</a>', self.get('/workspaces').text)


class WelcomeScript(unittest.TestCase):
    def run_node(self, scenario):
        if not shutil.which('node'):
            self.skipTest('node is needed')
        script = NODE_DOM + """
const assert = require('node:assert/strict');
global.localStorage = {getItem: () => JSON.stringify({handle: 'sam', token: 't'})};
global.navigator = {}; global.window = {isSecureContext: false}; global.location = {href: ''}; global.alert = () => {};
global.calls = []; global.reply = null; global.timers = [];
global.setTimeout = (f, ms) => { timers.push([f, ms]); return timers.length; }; global.clearTimeout = () => {};
global.root = new N('div'); global.document.getElementById = () => root;
global.fetch = (url, opts) => { calls.push([opts.method, url, opts.body && JSON.parse(opts.body)]); const r = reply(url, opts.method); return Promise.resolve({ok: r.ok !== false, json: () => Promise.resolve(r.body)}); };
global.facts = (bot, extra) => ({state: 'pending', steps: Object.assign({board: {address: 'http://127.0.0.1:8080', handle: 'sam'},
  forge: {configured: true, url: 'http://localhost:3000', handle: 'sam'}, bot: bot, team: [], general: 'admin'}, extra || {})});
global.bot = (state, extra) => Object.assign({handle: 'admin-1', harness: 'claude', role: 'Admin', state: state, words: state, reason: '', up: false}, extra || {});
global.tick = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };
global.show = async (d) => { calls.length = 0; timers.length = 0; reply = () => ({body: d}); (0, eval)(%s); await tick(); };
(async () => {
""" % json.dumps(welcome_web.WELCOME_JS) + scenario + """
})().catch(e => { console.error(e); process.exit(1); });
"""
        r = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_the_steps_follow_the_admin_bots_state(self):
        self.run_node("""
await show(facts(bot('starting')));
assert.ok(root.textContent.includes('is starting. This takes about a minute'), 'starting says so');
assert.ok(!root.textContent.includes('0 bots'), 'never a false zero');
assert.equal(timers.length, 1, 'it polls while the bot starts');
await show(facts(bot('needs login', {reason: 'logged out of claude'})));
assert.ok(root.textContent.includes('claude setup-token') && root.textContent.includes('CLAUDE_CODE_OAUTH_TOKEN'));
await show(facts(bot('working'), {team: [bot('working', {handle: 'dev-1', role: 'Developer'}), bot('starting', {handle: 'arch-1', role: 'Architect'})]}));
assert.ok(root.textContent.includes('The team is starting: 1 of 2 are running.'));
assert.equal(timers.length, 1, 'still polling: a team bot is starting');
await show(facts(bot('waiting'), {team: [bot('waiting', {handle: 'dev-1', role: 'Developer'})]}));
assert.ok(root.textContent.includes('Your team of 1 is running'));
assert.equal(timers.length, 0, 'it stops polling once everything runs');
await show(facts(bot('failing', {reason: 'cannot run its harness', log: '~/.config/bot_board/bots/admin-1/keeper.out'})));
assert.ok(root.textContent.includes('did not start') && root.textContent.includes('keeper.out'));
await show(facts(null));
assert.ok(root.textContent.includes('The admin bot is being set up'), 'no bot yet reads as starting, not as none');
""")

    def test_forge_text_stays_text_and_an_absent_forge_is_optional(self):
        self.run_node("""
await show(facts(bot('working'), {forge: {configured: true, url: 'javascript:alert(1)', handle: '<b>x</b>'}}));
assert.deepEqual(root.all('a').filter(a => (a.attrs.href || '').startsWith('javascript')), [], 'no javascript: link');
assert.ok(root.textContent.includes('<b>x</b>'), 'the handle is text');
assert.ok(!root.textContent.includes('PASSWORDVALUE'));
assert.ok(root.textContent.includes('~/.bot_board/forgejo-admin-password'), 'the password file is named, not its content');
await show(facts(bot('working'), {forge: {configured: false}}));
assert.ok(root.textContent.includes('No Forgejo is connected') && root.textContent.includes('optional'));
await show(facts(bot('working'), {forge: {configured: false, problem: 'FORGEJO_URL must be an http or https address'}}));
assert.ok(root.textContent.includes('FORGEJO_URL must be an http or https address'));
""")

    def test_skip_and_send_save_the_state_and_go_to_general(self):
        self.run_node("""
await show(facts(bot('working')));
const skip = root.all('button').find(b => b.textContent === 'Skip setup');
skip.click(); await tick();
assert.deepEqual(calls[1], ['PUT', '/api/first-run', {state: 'skipped'}]);
assert.equal(location.href, '/c/admin');
await show(facts(bot('working')));
const send = root.all('button').find(b => b.textContent === 'Send and open General');
assert.ok(root.all('textarea')[0].value.startsWith('Hello. Please explain how this team is put together'));
assert.ok(!root.all('textarea')[0].value.includes('@'), 'the first message mentions no one');
send.click(); await tick();
assert.deepEqual(calls[1], ['POST', '/api/messages', {channel: 'admin', body: root.all('textarea')[0].value}]);
assert.deepEqual(calls[2], ['PUT', '/api/first-run', {state: 'done'}]);
assert.equal(location.href, '/c/admin');
// a bot that did not start: nothing to send to yet
await show(facts(bot('failing')));
assert.equal(root.all('button').find(b => b.textContent === 'Send and open General').disabled, true);
""")


if __name__ == '__main__':
    unittest.main()
