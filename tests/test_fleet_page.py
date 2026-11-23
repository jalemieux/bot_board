import unittest
from html.parser import HTMLParser

from app import db, web


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
                page, sections = self.render([host(
                    'legacy', [], reporting=reporting,
                    update={'revision': 'v1.0.124', 'result': 'up to date'})])
                self.assertIn('checkout v1.0.124', page)      # under Details, folded until opened
                self.assertIn('<summary>Details</summary>', page)
                self.assertIn('up to date', sections['fleet-machines'])
                self.assertNotIn('checkout v1.0.124', sections['fleet-machines'])
                if reporting:
                    self.assertIn('before #238', page)

    def test_health_timestamps_are_localizable_and_diagnostics_stay_escaped(self):
        health = 'out of codex usage since 2026-10-17T06:57:20Z: <script>bad</script>'
        page, _ = self.render([host('live', [bot('limited', health=health)])])
        self.assertEqual(page.count('data-local-time datetime="2026-10-17T06:57:20Z"'), 1)
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', page)
        self.assertNotIn('<script>bad</script>', page)
        self.assertIn("timeZoneName: 'short'", page)
        self.assertIn('toLocaleString', page)
        self.assertEqual(web._fleet_detail('bad 2026-99-99T06:57:20Z <x>'),
                         'bad 2026-99-99T06:57:20Z &lt;x&gt;')

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
        self.assertIn('Needs you · 3', page)     # the usage limit, the login, the box gone dark
        self.assertIn('1 not reporting', page)
        self.assertNotIn('id="fleet-bots"', page)     # the bots are on the workspaces' Team tabs (#733)
        self.assertIn('5 up', sections['fleet-machines'])
        self.assertIn('not reporting', sections['fleet-machines'])
        self.assertIn('offline stopped reporting', sections['fleet-attention'])

    def test_usage_alert_is_grouped_and_says_nothing_needs_doing(self):
        reason = 'out of codex usage: weekly limit; resets Monday'
        page, sections = self.render([host('live', [
            bot('one', health=reason),
            bot('two', 'stopped', health=reason, want='running', set_by='op', set_at=db.now()),
        ])])
        attention = sections['fleet-attention']
        self.assertEqual(attention.count('codex usage limit reached'), 1)
        self.assertIn('once the limit resets', attention)
        self.assertIn('@one', attention)
        self.assertIn('@two', attention)
        self.assertIn('Stop them', attention)

    def test_a_machine_with_bots_can_be_drained(self):
        page, sections = self.render([host('live', [
            bot('ready', pool=True, serves='/saved/sessions'),
            bot('retired', 'stopped', health='out of codex usage: old limit', want='stopped',
                set_by='op', set_at=db.now()),
        ]), host('other', [])])
        self.assertNotIn('fleet-attention', sections)
        self.assertIn('class="drain"', page)

    def test_requested_start_and_failure_stay_in_view_with_a_fix(self):
        page, sections = self.render([host('live', [
            bot('starting', 'stopped', want='running', set_by='op', set_at=db.now()),
            bot('broken', 'stopped', want='running', problem='codex not installed',
                set_by='op', set_at=db.now()),
        ])])
        self.assertIn('Could not start 1 bot', sections['fleet-attention'])
        self.assertIn('codex not installed', sections['fleet-attention'])
        self.assertIn('Try again', sections['fleet-attention'])
        self.assertIn('Leave stopped', sections['fleet-attention'])

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
        self.assertIn('2 apps found</summary>', page)
        self.assertNotIn('Xcode', text, 'the app list is folded')
        self.assertIn('Xcode', page)
        self.assertIn('class="chip prob">claude missing', page)
        self.assertNotIn('pip bad', page.split('Host bots')[1].split('</div>')[0], 'off is not a warning')

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
        self.assertIn("name === 'FORGEJO_URL'", page)
        self.assertIn("if (val.type !== want) { val.value = '';", page)
        self.assertIn("type: isUrl(x.name) ? 'text' : 'password'", page)
        self.assertIn('<section class="fleet-settings" id="secrets"><h2>Shared secrets</h2>', page)
        self.assertNotIn('<summary>Shared secrets', page)
        self.assertIn("sEl('td', {'data-label': 'New secret'}, name, hint)", page)
        self.assertIn('<p id="secretok" class="sub" role="status" tabindex="-1">', page)

    def test_credentials_and_empty_machine_states(self):
        page, sections = self.render([host('empty', [], credentials=[
            dict(name='claude', state='missing', detail='Run claude /login'),
        ])])
        self.assertIn('Run claude /login', sections['fleet-attention'])
        # the bots it holds are not one more alert each
        held, held_sections = self.render([host('box', [bot('dex', health='logged out of codex')],
                                           credentials=[dict(name='codex', state='missing', detail='not logged in')])])
        self.assertIn('codex missing on box', held_sections['fleet-attention'])
        self.assertIn('0 up', sections['fleet-machines'])
        self.assertIn('No box has reported yet', self.render([])[0])

    def test_untrusted_names_and_health_are_escaped(self):
        page, _ = self.render([host('<machine>', [bot('<worker>', health='out of usage: <script>bad()</script>')])])
        self.assertNotIn('<script>bad()</script>', page)
        self.assertIn('&lt;script&gt;bad()&lt;/script&gt;', page)
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
