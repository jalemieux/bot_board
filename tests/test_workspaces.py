"""Workspaces: the isolation boundary, its channels, its bots, its knowledge base, and the
admin workspace that seeds the others. Run with python -m unittest discover -s tests."""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bare_board                                  # noqa: E402
from app import db, main, workspaces


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.before_start()
        self.client = TestClient(main.app)
        self.client.__enter__()
        bare_board.skip_first_run()      # these tests start from a bare board (#650)

    def before_start(self):
        pass

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind, workspace=None, expected=201):
        body = {'handle': handle, 'kind': kind}
        if workspace:
            body['workspace'] = workspace
        r = self.client.post('/api/agents', json=body)
        self.assertEqual(r.status_code, expected, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']} if expected == 201 else r.json()

    def call(self, method, path, data=None, auth=None, expected=200):
        r = self.client.request(method, path, json=data, headers=auth)
        self.assertEqual(r.status_code, expected, r.text)
        return r.json()

    def post(self, auth, body, channel=None, reply_to=None, expected=201, meta=None):
        data = {'body': body, 'meta': meta or {}}
        if channel:
            data['channel'] = channel
        if reply_to:
            data['reply_to'] = reply_to
        return self.call('POST', '/api/messages', data, auth=auth, expected=expected)


class Workspaces(Base):
    def setUp(self):
        super().setUp()
        self.human = self.register('jac', 'human')
        self.game = self.call('POST', '/api/workspaces', {'slug': 'game', 'name': 'Space Game', 'purpose': 'A game',
                              'seed': {'description': 'A space sandbox.', 'links': ['https://example.com/design'],
                                       'text': 'Factions: the Lorre and the Vane.'}}, auth=self.human, expected=201)['workspace']
        self.call('POST', '/api/workspaces', {'slug': 'other', 'name': 'Other', 'purpose': 'Something else'},
                  auth=self.human, expected=201)
        self.ada = self.register('ada', 'claude-code', 'game')
        self.milo = self.register('milo', 'codex', 'other')
        self.root = self.register('root', 'claude-code', 'admin')

    def test_a_workspace_comes_with_general_and_a_seed_thread_for_the_admin_bots(self):
        self.assertEqual(self.game['channel'], 'game')
        self.assertEqual([c['slug'] for c in self.game['channels']], ['game'])
        self.assertTrue(self.game['channels'][0]['home'])
        self.assertEqual(self.game['channels'][0]['name'], 'General')
        tid = self.game['seed_thread_id']
        self.assertTrue(tid)
        seed = self.call('GET', f'/api/messages/{tid}', auth=self.human)['message']
        self.assertEqual(seed['channel'], 'admin')
        self.assertEqual(seed['author'], 'jac')
        self.assertEqual(seed['meta']['kind'], 'seed')
        self.assertEqual(seed['meta']['workspace'], 'game')
        self.assertIsNone(seed['meta']['channel'])
        self.assertIn('https://example.com/design', seed['body'])
        self.assertIn('A space sandbox.', seed['body'])
        self.assertEqual(self.client.get(seed['attachments'][0]['url'], headers=self.root).text,
                         'Factions: the Lorre and the Vane.')
        # the admin bot sees it as open work; a game bot never does
        self.assertIn(tid, [m['id'] for m in self.call('GET', '/api/messages', auth=self.root)['messages']])
        self.assertNotIn(tid, [m['id'] for m in self.call('GET', '/api/messages', auth=self.ada)['messages']])
        self.call('POST', f'/api/threads/{tid}/worker', auth=self.root)
        # a workspace without seed material posts nothing
        other = self.call('GET', '/api/workspaces/other', auth=self.human)['workspace']
        self.assertIsNone(other['seed_thread_id'])
        # the seed kind is the board's own
        self.post(self.human, 'fake seed', channel='admin', meta={'kind': 'seed'}, expected=403)

    def test_the_status_counts_what_waits_on_a_human_in_each_workspace(self):
        # #643: the status of a human counts what waits on them in each workspace
        self.post(self.ada, 'ship it?', channel='game', meta={'needs': 'jac', 'ask': 'secret wording'})
        self.post(self.milo, 'and this?', channel='other', meta={'needs': 'jac'})
        self.post(self.milo, 'and that?', channel='other', meta={'needs': 'jac'})
        st = self.call('GET', '/api/status', auth=self.human)
        self.assertEqual(st['counts'], {'game': 1, 'other': 2})
        self.assertNotIn('counts', self.call('GET', '/api/status', auth=self.ada), 'only a human has asks waiting')
        waiting = st['waiting']
        self.assertEqual(sorted(w['workspace'] for w in waiting), ['game', 'other', 'other'], 'the board page filters the bell and Mentions by it')

    def test_the_board_page_opens_inside_a_workspace_and_search_stays_in_it(self):
        for path in ('/w/game', '/w/game/c/game', '/w/game/unread', '/w/game/mentions'):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(self.client.get('/w/nowhere').status_code, 404)
        self.assertIn('"workspaces": ["', self.client.get('/w/game').text.replace('\\u0022', '"'), 'the page knows the workspaces for the switcher')
        self.post(self.ada, 'zebra in game', channel='game')
        self.post(self.milo, 'zebra in other', channel='other')
        here = self.client.get('/search?q=zebra&workspace=game').text
        self.assertIn('zebra in game', here)
        self.assertNotIn('zebra in other', here)
        self.assertIn('Search all workspaces', here)
        both = self.client.get('/search?q=zebra').text
        self.assertTrue('zebra in game' in both and 'zebra in other' in both)

    def test_a_workspace_page_has_a_team_tab_and_a_channels_and_knowledge_tab(self):
        # #645: the page opens on Team (agreement, then bots by role); the old sections are the other tab
        from app import workspace_web
        for needle in ("['team', 'Team'], ['channels', 'Channels and knowledge']", "function teamView(", "function rolesView(",
                       "'Outside the agreement'", "/knowledge/team/agreement.md", "ev.key === 'Escape'",
                       "ev.key !== 'Tab'", "root.appendChild(ov)", "(danger ? cancel :", "fail(e.message)",
                       "'Pause team'", "'Resume team'", "The team is paused", "Promise.allSettled",
                       "function hostBlock(", "move back to a container…", "if (b.host_flag) d.appendChild(hostBlock(b, ctx))", "its Mac is not reporting",
                       "function stopDialog(", "body.threads = choice", "stopDialog(b, true, ctx)", "ctx.stopThreads(b)"):
            self.assertIn(needle, workspace_web.WS_JS)
        self.assertNotIn("botsSection", workspace_web.WS_JS)      # the Channels tab has no Bots card; the Team tab lists the bots

    def test_only_a_human_or_an_admin_bot_changes_the_team_agreement(self):
        # #622: the agreement is written out in every bot's guide, so a bot of the workspace cannot rewrite it
        path = '/api/workspaces/game/knowledge/team/agreement.md'
        rev = lambda: self.call('GET', path, auth=self.human)['document']['revision']
        doc = lambda r: {'body': '# Team agreement\n\n## Merge\n\nAnyone merges.\n', 'base_revision': r, 'reason': 't', 'sources': []}
        self.call('PUT', path, doc(rev()), auth=self.ada, expected=403)
        self.call('PUT', '/api/knowledge/team/agreement.md', doc(rev()), auth=self.ada, expected=403)
        self.call('DELETE', path, {'base_revision': rev(), 'reason': 't'}, auth=self.ada, expected=403)
        self.call('PUT', '/api/knowledge/lore/factions.md', {'body': 'x', 'base_revision': 0, 'reason': 't', 'sources': []},
                  auth=self.ada)    # its other documents, as before
        self.call('PUT', path, doc(rev()), auth=self.root)
        self.call('PUT', path, doc(rev()), auth=self.human)
        self.call('DELETE', path, {'base_revision': rev(), 'reason': 't'}, auth=self.human)

    def test_only_a_human_or_an_admin_bot_writes_a_proposed_agreement(self):
        # #646: a human applies a proposal as the agreement, so a workspace bot must not be able to plant one
        path = '/api/workspaces/game/knowledge/team/proposals/17.md'
        body = {'body': '<!-- base: 1 -->\n# Team agreement\n', 'base_revision': 0, 'reason': 't', 'sources': []}
        self.call('PUT', path, body, auth=self.ada, expected=403)
        self.call('PUT', '/api/knowledge/team/proposals/17.md', body, auth=self.ada, expected=403)
        self.call('PUT', path, body, auth=self.root)
        self.call('DELETE', path, {'base_revision': 1, 'reason': 't'}, auth=self.ada, expected=403)
        self.call('DELETE', path, {'base_revision': 1, 'reason': 't'}, auth=self.human)
        self.call('PUT', '/api/knowledge/team/notes.md', {'body': 'x', 'base_revision': 0, 'reason': 't', 'sources': []}, auth=self.ada)

    def test_team_setup_asks_an_admin_bot_to_work_out_the_workspaces_team_with_its_creator(self):
        def team_threads():
            return [m for m in self.call('GET', '/api/messages', auth=self.root)['messages'] if (m.get('meta') or {}).get('kind') == 'team_setup']
        self.assertEqual(team_threads(), [], 'nothing asked by default: the workspaces made in setUp have none')
        made = self.call('POST', '/api/workspaces', {'slug': 'crew', 'name': 'Crew', 'purpose': 'x', 'team_setup': True}, auth=self.human, expected=201)['workspace']
        [ask] = team_threads()
        self.assertEqual(made['team_thread_id'], ask['id'], 'the answer says where the conversation is')
        self.assertEqual((ask['channel'], ask['author'], ask['meta']['workspace']), ('crew', 'jac', 'crew'))
        self.assertEqual(ask['body'], 'Set up how the team of workspace Crew (crew) works together.',
                         'the creator reads one plain sentence, not instructions for the bot')
        self.assertNotIn(ask['id'], [m['id'] for m in self.call('GET', '/api/messages', auth=self.ada)['messages']], 'a game bot never sees it')
        crew_bot = self.register('crewbot', 'claude-code', 'crew')
        self.call('POST', f"/api/threads/{ask['id']}/worker", auth=crew_bot, expected=403)   # not the workspace's own bots
        self.call('POST', f"/api/threads/{ask['id']}/worker", auth=self.root)          # an admin bot takes it, in the new workspace
        self.assertEqual(self.call('GET', f"/api/threads/{ask['id']}/worker", auth=self.root)['worker'], 'root')
        self.post(self.human, 'fake team setup', channel='admin', meta={'kind': 'team_setup'}, expected=403)   # the board's own kind
        # a bot cannot create a workspace, so cannot ask; and the flag does nothing for the admin workspace itself
        self.call('POST', '/api/workspaces', {'slug': 'plain', 'name': 'Plain', 'purpose': 'x', 'team_setup': False}, auth=self.human, expected=201)
        plain = self.call('POST', '/api/workspaces', {'slug': 'plain2', 'name': 'Plain two', 'purpose': 'x'}, auth=self.human, expected=201)['workspace']
        self.assertNotIn('team_thread_id', plain)
        self.assertEqual(len(team_threads()), 1)

    def test_the_new_workspace_form_offers_the_team_setup_and_its_script_parses(self):
        import shutil
        import subprocess
        from app import workspace_web
        self.assertIn("name: 'team_setup', checked: ''", workspace_web.WS_JS, 'ticked by default')
        self.assertIn('team_setup: f.elements.team_setup.checked', workspace_web.WS_JS)
        self.assertIn("'/t/' + d.workspace.team_thread_id", workspace_web.WS_JS, 'the creator lands in the conversation')
        self.assertIn('.ws label.check{display:flex', workspace_web.WS_CSS if hasattr(workspace_web, 'WS_CSS') else open(workspace_web.__file__).read())
        if shutil.which('node'):
            path = os.path.join(self.tmp.name, 'ws.js')
            with open(path, 'w') as fh:
                fh.write(workspace_web.WS_JS)
            r = subprocess.run(['node', '--check', path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_team_panel_reads_the_roles_table_against_the_bots_that_exist(self):
        from app import workspace_web
        table = ('# Team\n\n## Roles\n\n| Role | Profile |\n|---|---|\n| Developer | developer |\n'
                 '| Architect | no-such-profile |\n| Security | security-engineer | 3 |\n\n## Other\n\n| x | y |\n')   # an older Bots column is ignored
        self.assertEqual(self.call('GET', '/api/workspaces/game/team', auth=self.human)['team']['roles'], [], 'seeded default has no roles yet')
        self.call('PUT', '/api/workspaces/game/knowledge/team/agreement.md', {'body': table, 'base_revision': 1, 'reason': 't', 'sources': []}, auth=self.human)    # over the seeded revision 1
        roles = self.call('GET', '/api/workspaces/game/team', auth=self.human)['team']['roles']
        self.assertEqual([(r['profile'], r['want'], r['exists']) for r in roles], [('developer', 1, True), ('no-such-profile', 1, False), ('security-engineer', 1, True)], 'the agreement counts no bots: a role wants one')
        self.assertEqual([r['missing'] for r in roles], [1, 0, 1], 'a profile that does not exist is the admin bot\'s to make first')
        self.assertEqual([r['claims'] for r in roles], ['open', '', 'mentioned'], 'a profile that takes no new work by default works when mentioned')
        db.conn().execute("INSERT INTO fleet_bots(handle,host,harness,run,set_by,set_at,workspace,profile) VALUES('newdev','b','claude',1,'jac','now','game','developer')")
        db.conn().commit()
        r = self.call('GET', '/api/workspaces/game/team', auth=self.human)['team']['roles'][0]
        self.assertEqual((r['starting'], r['missing']), (['newdev'], 0), 'a bot asked for and not yet registered counts')
        for needle in ("'/team'", "'/api/fleet/bots'", "ctx.add(role)", "No bot would be running in the role"):      # #645: the Team tab adds, stops and removes
            self.assertIn(needle, workspace_web.WS_JS)
        self.assertNotIn('channels:', workspace_web.WS_JS.split('add: function (role)')[1].split('var add = ')[0], 'the add dialog binds no channels')

    def test_a_proposal_reads_its_base_in_one_place_and_a_missing_base_is_stale(self):
        # #646: the Team tab shows what a proposal changes and applies only text it showed
        import shutil, subprocess
        from app import workspace_web
        if not shutil.which('node'):
            self.skipTest('node is needed')
        js = workspace_web.WS_JS
        code = js[js.index('function parseProposal('):js.index('function proposalDocs(')] + js[js.index('var HIDDEN'):js.index('function diffView(')]
        script = code + """
const assert = require('node:assert/strict');
assert.deepEqual(parseProposal('<!-- base: 7 -->\\n# Team\\n'), {base: 7, text: '# Team\\n'});
assert.equal(parseProposal('# Team\\n').base, null);
assert.equal(parseProposal('<!-- base: x -->\\n# Team').base, null);
assert.equal(parseProposal('intro <!-- base: 7 -->\\n# Team').base, null);
const d = lineDiff('a\\nb\\nc', 'a\\nc\\nd');
assert.deepEqual(d.map(x => x.t + ':' + x.s), ['ctx:a', 'del:b', 'ctx:c', 'add:d']);
assert.equal('plain text, | table |'.search(HIDDEN), -1);
for (const c of ['\\u200B', '\\u202E', '\\u00AD', '\\uFEFF', '\\u{E0041}']) assert.ok(('a' + c + 'b').search(HIDDEN) >= 0, c);
assert.equal(showHidden('a\\u200Bb'), 'a<U+200B>b');
assert.equal(showHidden('x\\u{E0041}'), 'x<U+E0041>');
"""
        r = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for needle in ("q.base !== p.base || r[1].document.revision !== rev", "p.base === null || p.base !== rev", "var rev = doc ? doc.revision : goneRev",
                       "Applying it would remove the roles"):
            self.assertIn(needle, js)

    def test_the_change_box_asks_the_admin_bot_in_the_workspaces_general_channel(self):
        # #646: the words become a thread whose first line the team-setup skill recognises
        from app import workspace_web
        js = workspace_web.WS_JS
        for needle in ("'Amendment request for ' + w.slug + ', agreement revision ' + rev + ':\\n' + words", "channel: w.channel",
                       "(admin.handle ? '\\n\\n@' + admin.handle : '')", "x.text || ('@' + x.mention", "Applying one proposal makes the others out of date"):
            self.assertIn(needle, js)
        # the skill and the page agree on the first line
        skill = (Path(__file__).resolve().parents[1] / 'docs/skills/team-setup/SKILL.md').read_text()
        self.assertIn('`Amendment request for <workspace>, agreement revision <n>:`', skill)

    def test_the_phone_rules_cascade_the_way_they_are_written(self):
        # #647: a base rule written after a media rule with the same specificity wins, so the back button vanished; and a
        # scrim behind its own sheet's background turned the panel grey
        from app import web, workspace_web
        css = workspace_web.WS_CSS
        base = css.index('.ws .kb-back{display:none')
        phone = css.index('.ws .kb-back{display:inline-flex')
        self.assertLess(base, phone, 'the phone rule must come after the base rule')
        self.assertNotIn('.ws .kb-back{display:none', css[phone:], 'nothing after the phone rule hides the button again')
        sel = '.ws #wsmenu:not([hidden])'
        self.assertIn(sel + '{position:fixed', css)
        self.assertIn('background:var(--panel)', css[css.index(sel + '{position:fixed'):][:400])
        self.assertIn('box-shadow:0 0 0 100vmax rgba(0,0,0,.45)', css)        # the dimming is the sheet's own outer shadow, so scrolling cannot lose it
        self.assertIn(sel + '::before{content:"";position:fixed;inset:0;z-index:-1}', css)
        self.assertNotIn('::after{content:"";position:absolute', css)
        self.assertIn('.tabbar{position:fixed;left:0;right:0;bottom:0;z-index:4;', web.APP_CSS, 'the tab bar sits under the top bar, so a sheet covers it')
        self.assertIn('z-index:5', web.TOPNAV_CSS)
        for text in (web.TOPNAV_RESPONSIVE_CSS, web.APP_CSS):       # one function writes every sheet
            self.assertIn('box-shadow:0 0 0 100vmax rgba(0,0,0,.45)', text)

    def test_the_page_and_the_server_refuse_the_same_characters(self):
        # #707: the rule is a class, not a list, and the two copies must agree on every code point
        import shutil, subprocess, unicodedata
        from app import workspace_web, workspaces
        if not shutil.which('node'):
            self.skipTest('node is needed')
        js = workspace_web.WS_JS
        code = js[js.index('var HIDDEN'):js.index('function showHidden(')]
        script = code + """
let out = [];
for (let cp = 0; cp <= 0x10FFFF; cp++) {
  if (cp >= 0xD800 && cp <= 0xDFFF) continue;
  HIDDEN.lastIndex = 0;
  if (HIDDEN.test(String.fromCodePoint(cp))) out.push(cp);
}
console.log(JSON.stringify(out));
"""
        r = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        page = set(json.loads(r.stdout))
        server = {cp for cp in range(0x110000) if not 0xD800 <= cp <= 0xDFFF and workspaces.hidden_char(chr(cp)) and chr(cp) not in '\n\t'}
        # the two Unicode databases may differ by a release for a code point Python does not know yet: those are unassigned here
        skew = {cp for cp in page ^ server if unicodedata.category(chr(cp)) == 'Cn'}
        self.assertEqual(sorted((page ^ server) - skew), [], 'the page and the server refuse different characters')
        for cp in (0x1B, 0x7F, 0x80, 0xAD, 0x200B, 0x202E, 0xFEFF, 0xFFF9, 0x180B, 0x1D173, 0x13430, 0x17B4, 0x034F, 0x3164, 0x2800, 0xFE0F, 0xE0041,
                   0x2028, 0x2029, 0xE000, 0x0378, 0x17B4, 0x180B):      # the line and paragraph separators, private use, unassigned, Khmer and Mongolian
            self.assertIn(cp, server, hex(cp))
        for cp in (0x20, 0x41, 0xE9, 0x2014, 0x4E2D, 0x1F600):      # text, an accent, a dash, a CJK character, an emoji
            self.assertNotIn(cp, server, hex(cp))
        self.assertIsNone(workspaces.hidden_char('line one\n\tline two'))

    def test_a_windows_line_ending_is_stored_as_a_newline_and_a_lone_carriage_return_is_refused(self):
        # #707: a carriage return is a control character; only the first half of CRLF is allowed, and it is not stored
        path = '/api/workspaces/game/knowledge/team/agreement.md'
        rev = self.call('GET', path, auth=self.human)['document']['revision']
        got = self.call('PUT', path, {'body': '# Team agreement\r\n\r\n## Merge\r\n\r\nA human merges.\r\n', 'base_revision': rev,
                                      'reason': 't', 'sources': []}, auth=self.human)
        self.assertEqual(got['document']['body'], '# Team agreement\n\n## Merge\n\nA human merges.\n')
        self.call('PUT', path, {'body': '# Team agreement\rA lone return', 'base_revision': got['document']['revision'], 'reason': 't',
                                'sources': []}, auth=self.human, expected=422)

    def test_every_team_document_the_board_ships_passes_the_rule(self):
        # #707: `seed` and `apply` write docs/team/ through kb_put, so a character the rule refuses there would break them
        from app import workspaces
        root = Path(__file__).resolve().parents[1] / 'docs' / 'team'
        files = sorted(root.rglob('*.md'))
        self.assertTrue(files)
        for f in files:
            self.assertIsNone(workspaces.hidden_char(f.read_text(encoding='utf-8').replace('\r\n', '\n')), str(f))

    def test_a_bot_that_waits_for_work_is_up_and_offers_stop_and_restart(self):
        # a bot that waits for work has a launcher that runs: the board says `waiting`, as fleet.UP does
        import shutil, subprocess
        from app import fleet, workspace_web
        if not shutil.which('node'):
            self.skipTest('node is needed')
        js = workspace_web.WS_JS
        code = js[js.index('function botUp('):js.index('function botRows(')]
        states = {st: fleet.bot_state({'status': st}) for st in ['waiting for work', 'running game', 'running game, stopping', 'waiting', 'stopped', 'moved', '']}
        script = code + """
const assert = require('node:assert/strict');
const states = %s;
for (const s of Object.keys(states)) assert.equal(botUp({state: states[s]}), states[s].up, s);
assert.equal(botUp({}), false);
""" % json.dumps(states)
        r = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([st for st, v in states.items() if v['up']], ['waiting for work', 'running game', 'running game, stopping', 'waiting'])
        for needle in ("text: 'details ▾'", "function drawer(", "url + '/profile'", "url + '/claims'", "url + '/channels'", "url + '/voice'", "url + '/reset'", "url + '/move'"):
            self.assertIn(needle, js)      # the drawer: details, and an action for each thing a manager changes

    def test_the_phone_rules_for_dialog_fields_are_not_outweighed_by_the_base_rules(self):
        # #18527: `.dlg textarea` (0,1,1) lost to `.ws textarea` and `.ws input[type=text]` further down, so iOS still zoomed
        from app import workspace_web
        css = workspace_web.WS_CSS
        phone = css[css.index('@media (max-width:700px){\n.ws table.bots'):]
        for rule in ('.ws .dlg select{min-height:44px;font-size:16px}', '.ws .dlg textarea,.ws .dlg input[type=text]{font-size:16px}',
                     '.ws .dlg label.check{min-height:44px', '.ws .dlg input[type=checkbox]{width:22px;height:22px}'):
            self.assertIn(rule, phone)
        self.assertIn('.ws input[type=text],.ws input[type=password],.ws textarea,.ws select{', css)      # the base rule these must outweigh

    def test_a_bot_sees_only_its_workspace(self):
        g = self.post(self.human, 'game work', channel='game')['message']
        o = self.post(self.human, 'other work', channel='other')['message']
        self.assertEqual([c['slug'] for c in self.call('GET', '/api/channels', auth=self.ada)['channels']], ['game'])
        self.assertEqual([m['id'] for m in self.call('GET', '/api/messages', auth=self.ada)['messages']], [g['id']])
        self.assertEqual([m['id'] for m in self.call('GET', '/api/messages', auth=self.milo)['messages']], [o['id']])
        self.call('GET', f"/api/messages/{o['id']}", auth=self.ada, expected=404)
        self.call('GET', f"/api/threads/{o['id']}", auth=self.ada, expected=404)
        self.call('GET', '/api/channels/other', auth=self.ada, expected=404)
        self.post(self.ada, 'sneaking in', channel='other', expected=403)
        self.call('POST', f"/api/threads/{o['id']}/worker", auth=self.ada, expected=403)
        self.call('POST', f"/api/threads/{g['id']}/worker", auth=self.ada)
        # search and the roster are scoped too; humans see everything
        self.assertEqual([m['id'] for m in self.call('GET', '/api/search?q=work', auth=self.ada)['messages']], [g['id']])
        self.assertEqual({a['handle'] for a in self.call('GET', '/api/agents', auth=self.ada)['agents']}, {'ada', 'jac'})
        self.assertEqual(len(self.call('GET', '/api/messages', auth=self.human)['messages']), 3)   # + the seed thread
        self.assertEqual([m['id'] for m in self.call('GET', '/api/messages?workspace=other', auth=self.human)['messages']], [o['id']])
        # a bot that names no channel posts in its General; one it names that does not exist is refused,
        # a human creates channels (#465)
        home = self.post(self.ada, 'hello')['message']
        self.assertEqual(home['channel'], 'game')
        refused = self.post(self.ada, 'a new place', channel='game.scratch', expected=422)
        self.assertEqual(refused['error'], 'no channel game.scratch in game; a human creates channels')
        self.call('POST', '/api/channels', {'slug': 'game.scratch', 'topic': 't'}, auth=self.ada, expected=403)
        self.call('GET', '/api/channels/game.scratch', auth=self.human, expected=404)
        self.call('POST', '/api/channels', {'slug': 'game.scratch', 'topic': 't', 'workspace': 'game'}, auth=self.human, expected=201)
        self.post(self.ada, 'a new place', channel='game.scratch')
        self.call('POST', '/api/channels', {'slug': 'game.scratch', 'topic': 'retopic'}, auth=self.ada, expected=201)
        self.assertEqual(self.call('GET', '/api/channels/game.scratch', auth=self.ada)['channel']['workspace'], 'game')
        self.call('GET', '/api/channels/game.scratch', auth=self.milo, expected=404)
        # a mention from another workspace does not reach the inbox
        self.post(self.human, '@ada look', channel='other')
        self.assertEqual([m['body'] for m in self.call('GET', '/api/inbox', auth=self.ada)['messages']], ['game work'])
        me = self.call('GET', '/api/me', auth=self.ada)
        self.assertEqual(me['workspace']['slug'], 'game')
        self.assertFalse(me['admin_bot'])
        self.assertTrue(self.call('GET', '/api/me', auth=self.root)['admin_bot'])

    def test_channels_carry_focus_and_references_that_only_humans_edit(self):
        ch = self.call('POST', '/api/workspaces/game/channels', {
            'slug': 'lore', 'name': 'Art & Lore', 'description': 'Factions and visuals',
            'focus': 'Keep factions consistent with lore/factions.md.',
            'references': [{'path': 'lore/INDEX.md', 'mandatory': True}, {'path': 'lore/factions.md'}]},
            auth=self.human, expected=201)['channel']
        self.assertEqual(ch['slug'], 'game.lore')
        got = self.call('GET', '/api/channels/game.lore', auth=self.ada)['channel']
        self.assertEqual(got['name'], 'Art & Lore')
        self.assertEqual(got['focus'], 'Keep factions consistent with lore/factions.md.')
        self.assertEqual(got['references'], [{'path': 'lore/INDEX.md', 'mandatory': True},
                                             {'path': 'lore/factions.md', 'mandatory': False}])
        self.assertEqual(got['workspace'], 'game')
        self.call('PATCH', '/api/workspaces/game/channels/lore', {'focus': 'changed'}, auth=self.ada, expected=403)
        self.call('PATCH', '/api/workspaces/game/channels/lore', {'focus': 'changed'}, auth=self.root, expected=403)
        self.call('PATCH', '/api/workspaces/game/channels/lore', {'focus': 'changed', 'references': []}, auth=self.human)
        self.assertEqual(self.call('GET', '/api/channels/game.lore', auth=self.ada)['channel']['focus'], 'changed')
        self.call('POST', '/api/workspaces/game/channels', {'slug': 'x', 'name': 'X', 'references': [{'path': '../etc'}]},
                  auth=self.human, expected=422)
        listed = {c['slug']: c for c in self.call('GET', '/api/workspaces/game', auth=self.human)['workspace']['channels']}
        self.assertEqual(set(listed), {'game', 'game.lore'})
        # a channel can be seeded too: the thread names it
        seeded = self.call('POST', '/api/workspaces/game/channels', {'slug': 'economy', 'name': 'Economy',
                           'seed': {'links': ['https://example.com/economy']}}, auth=self.human, expected=201)['channel']
        seed = self.call('GET', f"/api/messages/{seeded['seed_thread_id']}", auth=self.root)['message']
        self.assertEqual((seed['meta']['workspace'], seed['meta']['channel']), ('game', 'game.economy'))

    def test_the_moderator_reads_posts_and_mentions_everywhere_but_claims_only_in_admin(self):
        # #319: a game thread is invisible to another workspace's bot, and the moderator's to read
        tid = self.post(self.ada, 'economy question', channel='game')['message']['id']
        self.call('GET', f'/api/messages/{tid}', auth=self.milo, expected=404)
        self.assertEqual(self.call('GET', f'/api/threads/{tid}', auth=self.root)['messages'][0]['id'], tid)
        ids = lambda r: [m['id'] for m in r['messages']]
        self.assertIn(tid, ids(self.call('GET', '/api/messages', auth=self.root)))
        self.assertIn(tid, ids(self.call('GET', '/api/messages?workspace=game', auth=self.root)))
        self.assertNotIn(tid, ids(self.call('GET', '/api/messages?workspace=other', auth=self.root)))
        self.assertIn(tid, ids(self.call('GET', '/api/search?q=economy', auth=self.root)))
        self.assertIn('game', {c['slug'] for c in self.call('GET', '/api/channels', auth=self.root)['channels']})
        # it replies in the game channel, and the game bot is reached there
        reply = self.post(self.root, '@ada milo can review this', channel='game', reply_to=tid)
        self.assertNotIn('warnings', reply)
        self.assertIn(reply['message']['id'], ids(self.call('GET', '/api/inbox', auth=self.ada)))
        # a mention from the admin channel does not reach a game bot, and the sender is told
        warned = self.post(self.root, '@ada please look at the admin thread', channel='admin')
        self.assertEqual([(w['mention'], w['state']) for w in warned['warnings']], [('ada', 'elsewhere')])
        self.assertNotIn(warned['message']['id'], ids(self.call('GET', '/api/inbox', auth=self.ada)))
        self.assertNotIn('warnings', self.post(self.human, '@root a sweep please', channel='game'))
        # it claims nothing outside admin; the thread stays for the workspace's bots
        self.call('POST', f'/api/threads/{tid}/worker', auth=self.root, expected=403)
        self.call('POST', f'/api/threads/{tid}/worker', auth=self.ada)
        admin_tid = self.post(self.human, 'moderator, a sweep please', channel='admin')['message']['id']
        self.call('POST', f'/api/threads/{admin_tid}/worker', auth=self.root)
        # an ordinary bot is as scoped as before
        self.post(self.milo, 'hi', channel='game', expected=403)
        self.assertFalse(self.call('GET', '/api/me', auth=self.milo)['admin_bot'])
        self.assertTrue(self.call('GET', '/api/me', auth=self.root)['admin_bot'])

    def test_a_moderator_named_in_a_workspace_thread_holds_it_when_it_answers(self):
        # #737: jac wrote "@moderator ..." in a workspace channel; it answered but held nothing, so the reply after
        # that, with no mention, never reached it
        named = self.post(self.human, '@root a sweep please', channel='game')['message']['id']
        self.post(self.root, 'which sweep?', channel='game', reply_to=named)
        self.assertEqual(self.call('GET', f'/api/threads/{named}/worker', auth=self.root)['worker'], 'root')
        later = self.post(self.human, 'the one for the economy', channel='game', reply_to=named)['message']['id']
        self.assertIn(later, [m['id'] for m in self.call('GET', '/api/inbox', auth=self.root)['messages']])
        # a workspace bot cannot hand the moderator its thread by naming it (jean, #740)
        handed = self.post(self.ada, '@root please take this', channel='game')['message']['id']
        self.post(self.root, 'looking', channel='game', reply_to=handed)
        self.assertIsNone(self.call('GET', f'/api/threads/{handed}/worker', auth=self.root)['worker'])
        # a thread that does not name it is still not its to take
        other = self.post(self.ada, 'economy question', channel='game')['message']['id']
        self.post(self.root, 'I can look', channel='game', reply_to=other)
        self.assertIsNone(self.call('GET', f'/api/threads/{other}/worker', auth=self.root)['worker'])

    def test_a_channel_the_moderator_creates_lands_in_admin_unless_it_names_a_workspace(self):
        admin = self.call('GET', '/api/workspaces/admin', auth=self.human)['workspace']['id']
        slug_workspace = lambda slug: db.get_board(slug)['workspace_id']
        self.post(self.root, 'hello', channel='admin-new')
        self.assertEqual(slug_workspace('admin-new'), admin)
        self.call('POST', '/api/channels', {'slug': 'admin-other', 'topic': 't'}, auth=self.root, expected=201)
        self.assertEqual(slug_workspace('admin-other'), admin)
        self.call('POST', '/api/channels', {'slug': 'game-new', 'topic': 't', 'workspace': 'game'}, auth=self.root, expected=201)
        self.assertEqual(slug_workspace('game-new'), self.game['id'])
        # a human's still has no workspace
        self.post(self.human, 'hello', channel='human-new')
        self.assertIsNone(slug_workspace('human-new'))

    def test_knowledge_is_revisioned_scoped_and_shared_with_admin_bots(self):
        doc = self.call('PUT', '/api/knowledge/INDEX.md', {'body': '# Game\n\n- lore/factions.md', 'base_revision': 0,
                        'reason': 'first index', 'sources': ['12']}, auth=self.ada)['document']
        self.assertEqual((doc['path'], doc['revision'], doc['author']), ('INDEX.md', 1, 'ada'))
        self.call('PUT', '/api/knowledge/INDEX.md', {'body': 'stale write', 'base_revision': 0}, auth=self.ada, expected=409)
        doc = self.call('PUT', '/api/knowledge/INDEX.md', {'body': '# Game v2\n\n- lore/factions.md', 'base_revision': 1,
                        'reason': 'tightened'}, auth=self.ada)['document']
        self.assertEqual(doc['revision'], 2)
        self.call('PUT', '/api/knowledge/lore/factions.md', {'body': 'The Lorre are traders.', 'base_revision': 0}, auth=self.ada)
        self.assertEqual(self.call('GET', '/api/knowledge/INDEX.md', auth=self.ada)['document']['body'], '# Game v2\n\n- lore/factions.md')
        self.assertEqual(self.call('GET', '/api/knowledge/INDEX.md?revision=1', auth=self.ada)['document']['body'], '# Game\n\n- lore/factions.md')
        hist = self.call('GET', '/api/knowledge/INDEX.md/history', auth=self.ada)
        self.assertEqual([r['revision'] for r in hist['revisions']], [2, 1])
        self.assertEqual([d['path'] for d in self.call('GET', '/api/knowledge', auth=self.ada)['documents']], ['INDEX.md', 'lore/factions.md', 'team/agreement.md'])
        found = self.call('GET', '/api/knowledge?q=traders', auth=self.ada)['documents']
        self.assertEqual([d['path'] for d in found], ['lore/factions.md'])
        self.assertIn('traders', found[0]['snippet'])
        # another workspace's bot has its own, empty, knowledge base
        self.assertEqual([d['path'] for d in self.call('GET', '/api/knowledge', auth=self.milo)['documents']], ['team/agreement.md'], 'only the seeded agreement')
        self.call('GET', '/api/knowledge/INDEX.md', auth=self.milo, expected=404)
        self.call('GET', '/api/workspaces/game/knowledge', auth=self.milo, expected=403)
        # admin bots and humans reach it by name
        self.assertEqual(len(self.call('GET', '/api/workspaces/game/knowledge', auth=self.root)['documents']), 3)
        self.call('PUT', '/api/workspaces/game/knowledge/decisions/decisions.md',
                  {'body': 'Teal and rust palette.', 'base_revision': 0, 'reason': 'seeded'}, auth=self.root)
        self.assertEqual(self.call('GET', '/api/knowledge/decisions/decisions.md', auth=self.ada)['document']['author'], 'root')
        self.assertEqual(self.call('GET', '/api/workspaces/game/knowledge/INDEX.md', auth=self.human)['document']['revision'], 2)
        # delete keeps history
        self.call('DELETE', '/api/knowledge/lore/factions.md', {'base_revision': 1, 'reason': 'merged'}, auth=self.ada)
        self.call('GET', '/api/knowledge/lore/factions.md', auth=self.ada, expected=404)
        self.assertEqual(len(self.call('GET', '/api/knowledge/lore/factions.md/history', auth=self.ada)['revisions']), 2)
        self.assertEqual([d['path'] for d in self.call('GET', '/api/knowledge', auth=self.ada)['documents']], ['INDEX.md', 'decisions/decisions.md', 'team/agreement.md'])
        self.call('PUT', '/api/knowledge/bad path', {'body': 'x', 'base_revision': 0}, auth=self.ada, expected=422)
        self.call('PUT', '/api/knowledge/empty.md', {'body': '  ', 'base_revision': 0}, auth=self.ada, expected=422)
        self.assertEqual(self.call('GET', '/api/workspaces/game', auth=self.human)['workspace']['documents'], 3)

    def test_the_guide_depends_on_who_asks(self):
        self.assertIn('fleet message board', self.client.get('/agents.md', headers=self.ada).text)
        self.assertIn('Board administration', self.client.get('/agents.md', headers=self.root).text)
        self.assertIn('fleet message board', self.client.get('/agents.md').text)
        self.assertEqual(self.client.get('/admin/agents.md', headers=self.ada).status_code, 403)
        self.assertEqual(self.client.get('/admin/agents.md', headers=self.root).status_code, 200)
        api = self.call('GET', '/api', auth=self.root)
        self.assertIn('administration', api)
        self.assertNotIn('administration', self.call('GET', '/api', auth=self.ada))
        schema = self.call('GET', '/api/openapi.json')
        self.assertIn('/api/knowledge/{path}', schema['paths'])
        self.assertNotIn('/api/workspaces', schema['paths'])

    def test_bots_belong_to_one_workspace_which_humans_set(self):
        self.assertEqual(self.call('GET', '/api/workspaces/game', auth=self.human)['workspace']['bots'], ['ada'])
        self.call('PUT', '/api/workspaces/game/bots', {'bots': ['ada', 'milo']}, auth=self.human)
        self.assertEqual(self.call('GET', '/api/me', auth=self.milo)['workspace']['slug'], 'game')
        self.assertEqual(self.call('GET', '/api/workspaces/other', auth=self.human)['workspace']['bots'], [])
        self.call('PUT', '/api/workspaces/game/bots', {'bots': ['jac']}, auth=self.human, expected=422)
        self.call('PUT', '/api/workspaces/game/bots', {'bots': ['ada']}, auth=self.ada, expected=403)
        self.call('GET', '/api/workspaces', auth=self.ada, expected=403)
        self.assertEqual({w['slug'] for w in self.call('GET', '/api/workspaces', auth=self.root)['workspaces']}, {'admin', 'game', 'other'})
        self.register('nobody', 'claude-code', 'nowhere', expected=404)
        # a bot of no workspace (a direct client, or one from before) sees the board as before
        lost = self.register('lost', 'claude-code')
        self.assertIsNone(self.call('GET', '/api/me', auth=lost)['workspace'])
        self.assertIn('game', [c['slug'] for c in self.call('GET', '/api/channels', auth=lost)['channels']])
        self.post(lost, 'anyone?', channel='game')
        self.post(lost, 'anyone?', expected=422)
        self.call('GET', '/api/knowledge', auth=lost, expected=400)

    def test_archiving_a_workspace_releases_its_bots(self):
        self.call('PATCH', '/api/workspaces/other', {'archived': True}, auth=self.human, expected=422)
        self.call('PATCH', '/api/workspaces/other', {'archived': True, 'archive_confirm': True}, auth=self.human)
        self.post(self.human, 'too late', channel='other', expected=403)
        self.assertTrue(self.call('GET', '/api/workspaces/other', auth=self.human)['workspace']['archived'])
        self.register('late', 'claude-code', 'other', expected=409)


class FleetWorkspace(Base):
    """A bot created from /fleet belongs to the workspace picked there."""

    def setUp(self):
        super().setUp()
        self.human = self.register('op', 'human')
        self.keeper = self.register('keeper-box', 'keeper')
        self.call('POST', '/api/workspaces', {'slug': 'falco', 'name': 'Falco', 'purpose': 'Bugs'}, auth=self.human, expected=201)
        self.report(bots=[])

    def report(self, bots):
        r = self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={'bots': bots})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_a_new_bot_needs_a_workspace_and_the_keeper_is_told_it(self):
        self.call('POST', '/api/fleet/bots', {'host': 'box', 'harness': 'claude'}, auth=self.human, expected=422)
        self.call('POST', '/api/fleet/bots', {'host': 'box', 'harness': 'claude', 'workspace': 'ghost'}, auth=self.human, expected=409)
        handle = self.call('POST', '/api/fleet/bots', {'host': 'box', 'harness': 'claude', 'workspace': 'falco'}, auth=self.human)['handle']
        self.call('PUT', '/api/fleet/bots/newbie', {'host': 'box', 'run': True, 'harness': 'claude'}, auth=self.human, expected=409)
        want = self.report(bots=[])['want']
        self.assertEqual([(w['handle'], w['workspace']) for w in want], [(handle, 'falco')])
        # bin/bot registers with that workspace, as the keeper passes --workspace
        bot = self.register(handle, 'claude-code', 'falco')
        self.assertEqual(self.call('GET', '/api/me', auth=bot)['workspace']['slug'], 'falco')
        self.report(bots=[{'handle': handle, 'harness': 'claude', 'status': 'waiting', 'pool': True}])
        self.assertEqual(self.call('GET', '/api/workspaces/falco', auth=self.human)['workspace']['bots'], [handle])

    def test_a_bot_that_registered_without_a_workspace_joins_it_when_its_box_reports(self):
        handle = self.call('POST', '/api/fleet/bots', {'host': 'box', 'harness': 'claude', 'workspace': 'falco'}, auth=self.human)['handle']
        bot = self.register(handle, 'claude-code')
        self.assertIsNone(self.call('GET', '/api/me', auth=bot)['workspace'])
        self.report(bots=[{'handle': handle, 'harness': 'claude', 'status': 'waiting', 'pool': True}])
        self.assertEqual(self.call('GET', '/api/me', auth=bot)['workspace']['slug'], 'falco')


class Migration(Base):
    """A board from before: projects, focus groups, a bot pool and published context become
    workspaces, channels, bot assignments and knowledge documents, in place."""

    def before_start(self):
        c = sqlite3.connect(db.DB_PATH)
        c.executescript(db.SCHEMA.replace(",\n    workspace_id INTEGER\n);", "\n);", 1).replace(
            ",\n    workspace_id INTEGER,\n    name       TEXT NOT NULL DEFAULT '',\n    focus      TEXT NOT NULL DEFAULT '',\n"
            "    refs       TEXT NOT NULL DEFAULT '[]',\n    seed_thread_id INTEGER,\n    archived   INTEGER NOT NULL DEFAULT 0\n);", "\n);", 1)
            .replace("CREATE INDEX IF NOT EXISTS idx_boards_workspace ON boards(workspace_id, kind);", ""))
        c.executescript("""
        CREATE TABLE project_admins (agent_id INTEGER PRIMARY KEY, granted_at TEXT NOT NULL);
        CREATE TABLE projects (id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,
            purpose TEXT NOT NULL, board_id INTEGER NOT NULL UNIQUE, owner_id INTEGER NOT NULL,
            repositories TEXT NOT NULL DEFAULT '[]', operators TEXT NOT NULL DEFAULT '[]',
            archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE project_groups (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, slug TEXT NOT NULL,
            name TEXT NOT NULL, purpose TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '', related_groups TEXT NOT NULL DEFAULT '[]',
            board_id INTEGER NOT NULL UNIQUE, archived INTEGER NOT NULL DEFAULT 0, seed_thread_id INTEGER,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE context_revisions (id INTEGER PRIMARY KEY, board_id INTEGER NOT NULL, base_revision INTEGER NOT NULL,
            revision INTEGER, status TEXT NOT NULL, body TEXT NOT NULL, sources TEXT NOT NULL, reason TEXT NOT NULL,
            author_id INTEGER NOT NULL, approved_by INTEGER, created_at TEXT NOT NULL, published_at TEXT);
        CREATE TABLE project_pools (project_id INTEGER PRIMARY KEY, configured INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE project_pool_bots (agent_id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL);
        INSERT INTO agents (id, handle, token_hash, kind, created_at) VALUES (1, 'jac', 'h1', 'human', 't'), (2, 'ada', 'h2', 'claude-code', 't'), (3, 'free', 'h3', 'codex', 't');
        INSERT INTO project_admins VALUES (1, 't');
        INSERT INTO boards (id, slug, topic, kind, created_at) VALUES (1, 'game', 'the game', 'channel', 't'), (2, 'game.art', 'art', 'channel', 't'),
            (3, 'lobby', '', 'channel', 't'), (4, 'help', '', 'channel', 't');
        INSERT INTO projects VALUES (1, 'game', 'Game', 'A game', 1, 1, '[]', '[]', 0, 't', 't');
        INSERT INTO project_groups VALUES (1, 1, 'art', 'Art', 'Consistent art', 'Assets and style', '[]', 2, 0, NULL, 't', 't');
        INSERT INTO context_revisions VALUES (1, 1, 0, 1, 'published', 'Project context v1', '["a"]', 'r', 1, 1, 't', 't'),
            (2, 1, 1, 2, 'published', 'Project context v2', '["b"]', 'r', 1, 1, 't', 't'),
            (3, 2, 0, 1, 'published', 'Palette: teal and rust.', '["c"]', 'r', 2, 1, 't', 't'),
            (4, 2, 1, NULL, 'draft', 'a draft', '[]', 'r', 2, NULL, 't', NULL);
        INSERT INTO project_pools VALUES (1, 1, 1);
        INSERT INTO project_pool_bots VALUES (2, 1);
        """)
        c.commit()
        c.close()

    def test_projects_groups_pools_and_context_become_workspaces(self):
        c = db.conn()
        names = {r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('workspaces', names)
        self.assertNotIn('projects', names)
        self.assertNotIn('project_groups', names)
        ws = workspaces.find('game')
        self.assertEqual(workspaces.home_slug(ws), 'game')
        chans = {ch['slug']: ch for ch in workspaces.channels_of(ws)}
        self.assertEqual(chans['game']['name'], 'General')
        self.assertEqual(chans['game.art']['name'], 'Art')
        self.assertEqual(chans['game.art']['focus'], 'Consistent art\n\nAssets and style')
        self.assertEqual(chans['game.art']['references'], [{'path': 'art/INDEX.md', 'mandatory': False}])
        self.assertEqual(db.get_agent_by_handle('ada')['workspace_id'], ws['id'])
        self.assertIsNone(db.get_agent_by_handle('free')['workspace_id'])
        self.assertEqual(workspaces.kb_get(ws, 'INDEX.md')['body'], 'Project context v2')
        self.assertEqual(workspaces.kb_get(ws, 'art/INDEX.md')['body'], 'Palette: teal and rust.')
        self.assertEqual([d['path'] for d in workspaces.kb_list(ws)['documents']], ['INDEX.md', 'art/INDEX.md', 'team/agreement.md'])
        # fleet channels are left as they were, attached to no workspace: no bot sees them
        self.assertIsNone(db.get_board('lobby')['workspace_id'])
        self.assertTrue(workspaces.is_admin(db.get_agent_by_handle('jac')))
        # the admin workspace was created for the admin that was there
        self.assertEqual(workspaces.kb_get(workspaces.find('admin'), 'INDEX.md')['revision'], 1)
        # a second start changes nothing
        db.init()
        self.assertEqual(len(workspaces.kb_history(ws, 'INDEX.md')['revisions']), 1)


if __name__ == '__main__':
    unittest.main()
