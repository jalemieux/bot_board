import unittest
from html.parser import HTMLParser

from app import db, versions, web


class FleetSections(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.sections = {}
        self.stack = []
        self.hidden = 0
        self.section = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('section', 'details', 'div'):
            self.stack.append((tag, self.section, self.hidden))
            if attrs.get('id', '').startswith(('fleet-', 'box-')):
                self.section = attrs['id']
            if tag == 'details' and 'open' not in attrs:
                self.hidden += 1

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag:
            _, self.section, self.hidden = self.stack.pop()

    def handle_data(self, data):
        if self.section and not self.hidden:
            self.sections[self.section] = self.sections.get(self.section, '') + data


def bot(handle, status='waiting', **extra):
    return dict(handle=handle, status=status, harness='codex', **extra)


def host(name, bots, reporting=True, **extra):
    return dict(name=name, bots=bots, reporting=reporting, reported_at=db.now(),
                keeper='keeper-' + name, cores=8, load=0.3,
                mem_total_mb=16000, mem_free_mb=12000, **extra)


class FleetPage(unittest.TestCase):
    def test_legacy_keeper_handle_with_update_renders_online_and_offline(self):
        for reporting in (True, False):
            with self.subTest(reporting=reporting):
                hosts = [host('legacy', [], reporting=reporting, update={'revision': 'v1.0.124', 'result': 'up to date'})]
                page, sections = self.render(hosts)
                self.assertIn('checkout v1.0.124', page)      # under Details, folded until opened
                self.assertIn('<summary>Details</summary>', page)
                self.assertIn('up to date', sections['fleet-machines'])
                self.assertNotIn('checkout v1.0.124', sections['fleet-machines'])
                if reporting:
                    # the Versions section is drawn by the page's script from /api/status (#888); its rows say it
                    self.assertTrue(any('before #238' in r['note'] for r in versions.rows(hosts)))

    def render(self, hosts):
        page = web.fleet_page('board', hosts)
        return page, FleetSections(page).sections

    def test_active_bots_lead_and_counts_match_what_they_do(self):
        page, sections = self.render([
            host('live', [bot('ready'), bot('busy', 'running game'),
                          bot('limited', health='out of codex usage: resets Monday'),
                          bot('login', health='logged out of codex'),
                          bot('leaving', 'running game, stopping'), bot('old', 'stopped')]),
            host('offline', [bot('ghost'), bot('gone', 'stopped')], reporting=False),
        ])
        self.assertIn('1 not reporting', page)
        self.assertNotIn('id="fleet-bots"', page)     # the bots are on the workspaces' Team tabs (#733)
        self.assertIn('5 up', sections['fleet-machines'])
        self.assertIn('not reporting', sections['fleet-machines'])

    def test_a_machine_with_bots_can_be_drained(self):
        page, sections = self.render([host('live', [
            bot('ready', pool=True, serves='/saved/sessions'),
            bot('retired', 'stopped', health='out of codex usage: old limit', want='stopped',
                set_by='op', set_at=db.now()),
        ]), host('other', [])])
        self.assertIn('class="drain"', page)

    def test_a_machine_is_one_short_row_that_says_how_its_bots_run(self):
        mac = host('mac', [bot('a'), bot('b', 'stopped')], os='Darwin 25.5.0',
                   update={'revision': 'v1', 'result': "waiting for the board's release commit"},
                   runtime={'setting': 'docker', 'state': 'ok', 'detail': 'docker 29.5.2'},
                   host_bots=False, apps=[{'name': 'Xcode', 'version': '26'}, {'name': 'Zed', 'version': '1'}],
                   credentials=[dict(name='gh', state='ok'), dict(name='claude', state='missing')])
        bare = host('bare', [], runtime={'setting': 'host', 'state': 'ok'})
        page, sections = self.render([mac, bare])
        text = sections['fleet-machines']
        self.assertIn('in containers', text)
        self.assertIn('on the host', text)
        self.assertIn('update waiting', text)
        self.assertNotIn('stopped', text.replace('stopped reporting', ''), 'a stopped bot has no place on this page')
        self.assertNotIn('Xcode', text, 'the Mac card, with its app list, is drawn by the script (tests/test_host_card_script.py)')
        self.assertIn('class="chip prob">claude missing', page)

    def test_the_update_and_runtime_pills_read_each_result_the_keeper_writes(self):
        for result, word, fine in (
                ('up to date', 'up to date', True), ('updated abc123 -> def456', 'up to date', True),
                ("waiting for the board's release commit", 'update waiting', True),
                ("ahead of the board's release abc; waiting for it", 'update waiting', True),
                ("the board's release abc is not on origin yet", 'update waiting', True),
                ('cannot move to abc: dirty', 'update needs a look', False),
                ('abc failed its checks and was rolled back to def: x', 'update needs a look', False),
                ('fetch failed: no route', 'update needs a look', False),
                ('update check failed: boom', 'update needs a look', False)):
            with self.subTest(result=result):
                page, sections = self.render([host('m', [], update={'revision': 'v1', 'result': result})])
                self.assertIn(word, sections['fleet-machines'])
                pill = page.split(f'title="{web.e(result)}">')[1].split('</span>')[0]
                self.assertEqual(pill, word)
                self.assertEqual('class="pill prob" title="' + web.e(result) in page, not fine)
        for state, text, prob in (('ok', 'in containers', False), ('missing', 'runtime missing', True), ('down', 'runtime down', True)):
            with self.subTest(runtime=state):
                page, sections = self.render([host('m', [], runtime={'setting': 'docker', 'state': state, 'detail': 'why'})])
                self.assertIn(text, sections['fleet-machines'])
                self.assertEqual(f'class="pill prob" title="why">{text}' in page, prob)

    def test_the_secrets_script_has_the_follow_ups_of_694(self):
        # the script itself runs in a browser (the Playwright tests skip without one): check what the page carries
        page = web.fleet_page('board', [])
        self.assertIn("'Selected: copy it'", page)
        self.assertNotIn('Press Ctrl+C', page)
        self.assertIn("ta.remove(); copy.focus();", page)
        self.assertEqual(page.count("'class': 'primary', text: '"), 2, 'Save and Add are the primary buttons')
        self.assertIn("button.primary{background:var(--accent)", page)
        self.assertIn("name.value.trim() === plainName", page)
        self.assertIn("if (val.type !== want) { val.value = '';", page)
        self.assertIn("type: x.plain ? 'text' : 'password'", page)
        self.assertNotIn("isUrl", page)
        self.assertLess(page.index("var plainName = d.plain_name"), page.index("hint = sEl("), "the hint's text is defined before it is used")
        self.assertNotIn("FORGEJO_URL", page.split("function secretCall")[1].split("listSecrets();")[0], "the script never spells the name")
        self.assertIn('<section class="fleet-settings" id="secrets"><h2>Shared secrets</h2>', page)
        self.assertNotIn('<summary>Shared secrets', page)
        self.assertIn("sEl('td', {'data-label': 'New secret'}, name, hint)", page)
        self.assertIn('<p id="secretok" class="sub" role="status" tabindex="-1">', page)

    def test_the_configuration_page_is_an_empty_shell_that_reads_the_api(self):
        # #861 (part of #845): the route reads no host, profile or skill for it; the page's script reads /api/fleet/secrets
        hosts = [host('SECRETBOX', [])]
        page = web.configuration_page('board')
        for gone in ('SECRETBOX', 'SECRETPROFILE', 'SECRETSKILL', 'id="tab-machines"', 'id="tab-roles"', 'id="tab-skills"', 'fleet-machines'):
            self.assertNotIn(gone, page)
        for kept in ('<h1>Configuration</h1>', 'data-panes="tab-settings"', 'id="secrets"', 'id="secretlist"', 'id="fleet-add-machine"',
                     "'/api/fleet/secrets'"):
            self.assertIn(kept, page)
        self.assertIn('id="tab-machines"', web.fleet_page('board', hosts, page='machines'), 'Machines still draws its own data (#872)')
        # the Claude login card says when the token is near its year (#880), by the rule of fleet.claude_token_due
        self.assertIn("d.claude_token_due", page)       # the server decides when it is near: no 14 in the script
        self.assertNotIn("left < 14", page)
        for said in ("'expires today'", "It expired on ", "Replace it before then.", "status.bad{background:var(--accent)"):      # sage's four points (#880)
            self.assertIn(said, page)

    def test_the_skills_page_is_an_empty_shell_that_reads_the_api(self):
        # #874 (part of #845): the route reads no skill or profile for it; the script reads /api/skills and /api/profiles
        skill = {"name": "SECRETSKILL", "description": "d", "revision": 1, "updated_at": "2026-01-01T00:00:00+00:00", "author": "jac",
                 "base": False, "used_by": []}
        page = web.skills_page('board')
        for gone in ('SECRETSKILL', 'SECRETPROFILE', 'SECRETBOX', 'id="tab-machines"', 'id="tab-roles"'):
            self.assertNotIn(gone, page)
        for kept in ('<h1>Skills</h1>', 'data-panes="tab-skills"', 'id="skills-list"', 'id="skill-add"', "get('/api/skills')", "get('/api/profiles')"):
            self.assertIn(kept, page)
        self.assertNotIn('SECRETSKILL', web.fleet_page('board', [], skills=[skill], page='roles'), 'Roles is a shell too (#873)')

    def test_the_top_bar_has_no_connect_your_harness_link(self):
        page = web.fleet_page('board', [])
        self.assertNotIn('href="/onboard" class="x"', page)
        self.assertIn('id="themerow"', page, 'the phone menu keeps its theme row')

    def test_a_board_on_localhost_says_why_no_other_machine_can_use_its_command(self):
        # #552: the Add a machine command carries the address the board was opened at; on localhost it cannot work, so both
        # places say so and how to change it, instead of leaving the person to find an address
        for page in (web.onboard_page('b', 'http://127.0.0.1:8080', False), web.onboard_page('b', 'http://localhost:8080', False),
                     web.fleet_page('b', [])):
            self.assertIn('listens on this machine only', page)
            self.assertIn('Reach your fleet', page)
            self.assertIn('bot_board expose', page)      # #796: the one command that does it
            self.assertNotIn('BIND=', page, 'no command that breaks this machine\'s own keeper')
            self.assertIn('Anyone who can reach the board can use it', page)
        remote = web.onboard_page('b', 'http://box.example.ts.net:8080', False)
        self.assertIn('Your board address is already filled in.', remote)
        self.assertNotIn('listens on this machine only', remote)

    def test_empty_machine_states(self):
        page, sections = self.render([host('empty', [], credentials=[dict(name='claude', state='missing', detail='Run claude /login')])])
        self.assertIn('0 up', sections['fleet-machines'])
        self.assertNotIn('Run claude /login', page, 'the alert is the script\'s (#887)')
        self.assertIn('No box has reported yet', self.render([])[0])

    def test_untrusted_names_and_health_are_escaped(self):
        page, _ = self.render([host('<machine>', [bot('<worker>', health='out of usage: <script>bad()</script>')])])
        self.assertNotIn('<script>bad()</script>', page)
        self.assertNotIn('<machine>', page)
        self.assertIn('&lt;machine&gt;', page)

    def test_add_machine_section_is_present_even_without_hosts(self):
        page, _ = self.render([])
        setup = page[page.index('id="fleet-add-machine"'):]
        self.assertIn('Before you start', setup)
        self.assertIn('Python 3', setup)
        self.assertIn('gh auth login', setup)
        self.assertIn('Open a terminal on that machine', setup)
        self.assertIn('id="machine-copy" disabled', page)
        self.assertIn('id="machine-status"', page)
        self.assertIn('location.origin', web.FLEET_JS)
        self.assertNotIn('KEEPER_OPERATORS', web.FLEET_JS)
        self.assertNotIn('board.example.invalid', page)

    def test_refresh_keeps_details_open_without_hiding_recovery(self):
        import json
        import shutil
        import subprocess
        if not shutil.which('node'):
            self.skipTest('node required for fleet refresh behavior')
        script = web.FLEET_JS[web.FLEET_JS.index('// Preserve expanded worker details'):]
        harness = r'''
const assert = require('node:assert/strict');
let tick, reloads = 0, inputs = [];
const saved = new Map();
global.sessionStorage = {
  getItem: key => saved.get(key),
  setItem: (key, value) => saved.set(key, value),
  removeItem: key => saved.delete(key)
};
const detail = {
  open: true,
  closest: selector => selector === 'tr' ? {querySelector: () => ({getAttribute: () => '/a/olga'})} : {id: 'fleet-running'},
  querySelector: () => ({textContent: 'Details for @olga'})
};
global.document = {
  activeElement: null,
  querySelector: () => null,
  querySelectorAll: selector => selector.endsWith('input') ? inputs : [detail]
};
global.location = {reload: () => reloads++};
global.setInterval = fn => { tick = fn; };
'''
        checks = r'''
tick();
assert.equal(reloads, 1, 'expanded details must not block recovery refresh');
assert.equal(JSON.parse(saved.get('fleet.expanded')).length, 1);
detail.open = false;
'''
        checks += '\neval(' + json.dumps(script) + ');\n'
        checks += r'''
assert.equal(detail.open, true, 'expanded details restored after refresh');
document.activeElement = {tagName: 'INPUT'};
tick(); assert.equal(reloads, 1, 'active form protected');
document.activeElement = null;
inputs = [{value: 'unsaved bot name'}];
tick(); assert.equal(reloads, 1, 'unsaved form protected');
'''
        subprocess.run(['node', '-e', harness + script + checks], check=True, capture_output=True, text=True)
