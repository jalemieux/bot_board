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
    def test_keeper_update_failures_and_worker_versions_are_visible_and_escaped(self):
        page, sections = self.render([host('live', [bot('busy', 'running game', launcher_revision='123456789012abc')],
            updates={'installed': 'abcdef0123456789', 'available': 'fedcba9876543210',
                     'checked_at': db.now(), 'result': 'rolled back', 'error': '<unsafe>'})])
        self.assertIn('abcdef012345', sections['box-live'])
        self.assertIn('fedcba987654', sections['box-live'])
        self.assertIn('rolled back', sections['box-live'])
        self.assertIn('&lt;unsafe&gt;', page)
        self.assertNotIn('<unsafe>', page)
        self.assertIn('launcher 123456789012', page)

    def test_health_timestamps_are_localizable_and_diagnostics_stay_escaped(self):
        health = 'out of codex usage since 2026-10-17T06:57:20Z: <script>bad</script>'
        page, _ = self.render([host('live', [bot('limited', health=health)])])
        self.assertEqual(page.count('data-local-time datetime="2026-10-17T06:57:20Z"'), 2)
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', page)
        self.assertNotIn('<script>bad</script>', page)
        self.assertIn("timeZoneName: 'short'", page)
        self.assertIn('toLocaleString', page)
        self.assertEqual(web._fleet_detail('bad 2026-99-99T06:57:20Z <x>'),
                         'bad 2026-99-99T06:57:20Z &lt;x&gt;')

    def render(self, hosts):
        page = web.fleet_page('board', hosts)
        return page, FleetSections(page).sections

    def test_available_counts_exclude_blocked_transitions_history_and_stale_hosts(self):
        page, sections = self.render([
            host('live', [bot('ready'), bot('busy', 'running game'),
                          bot('limited', health='out of codex usage: resets Monday'),
                          bot('login', health='logged out of codex'),
                          bot('leaving', 'running game, stopping'), bot('old', 'stopped')]),
            host('offline', [bot('ghost')], reporting=False),
        ])
        self.assertIn('1</b> running', page)
        self.assertIn('1</b> idle', page)
        self.assertIn('2</b> need attention', page)
        self.assertIn('1 of 2 boxes', page)
        self.assertIn('@ready', sections['fleet-idle'])
        self.assertIn('@busy', sections['fleet-running'])
        for handle in ('limited', 'login', 'leaving', 'old', 'ghost'):
            self.assertNotIn('@' + handle, sections['fleet-idle'])
            self.assertNotIn('@' + handle, sections['fleet-running'])
        self.assertIn('@leaving', sections['fleet-pending'])
        self.assertIn('2 available · 2 blocked · 5 launchers alive', sections['box-live'])
        self.assertIn('availability unknown', sections['box-offline'])
        self.assertNotIn('@ghost', sections['box-offline'])

    def test_usage_alert_is_grouped_and_stopped_usage_block_is_visible(self):
        reason = 'out of codex usage: weekly limit; resets Monday'
        page, sections = self.render([host('live', [
            bot('one', health=reason),
            bot('two', 'stopped', health=reason, want='running', set_by='op', set_at=db.now()),
        ])])
        self.assertEqual(sections['fleet-attention'].count('codex: usage limit reached'), 1)
        self.assertEqual(sections['fleet-attention'].count(reason), 1)
        self.assertIn('View 2 workers', page)
        self.assertIn('0</b> running', page)
        self.assertIn('0</b> idle', page)
        self.assertNotIn('id="fleet-history"', page)

    def test_history_and_worker_controls_are_collapsed_but_preserved(self):
        page, sections = self.render([host('live', [
            bot('ready', pool=True, serves='/saved/sessions'),
            bot('retired', 'stopped', health='out of codex usage: old limit', want='stopped',
                set_by='op', set_at=db.now()),
        ]), host('other', [])])
        self.assertNotIn('fleet-attention', sections)
        self.assertIn('1 record', page)
        self.assertNotIn('@retired', sections.get('fleet-history', ''))
        self.assertNotIn('/saved/sessions', sections['fleet-idle'])
        self.assertIn('data-kill="1"', page)
        self.assertIn('class="move" data-handle="ready"', page)
        self.assertIn('data-handle="retired" data-host="live" data-run="1"', page)
        self.assertIn('class="drain"', page)

    def test_requested_start_and_failure_do_not_disappear_into_history(self):
        page, sections = self.render([host('live', [
            bot('starting', 'stopped', want='running', set_by='op', set_at=db.now()),
            bot('broken', 'stopped', want='running', problem='codex not installed',
                set_by='op', set_at=db.now()),
        ])])
        self.assertIn('@starting', sections['fleet-pending'])
        self.assertIn('codex not installed', sections['fleet-attention'])
        self.assertNotIn('id="fleet-history"', page)
        self.assertIn('title="withdraw the start">cancel', page)

    def test_failed_last_run_keeps_worker_available(self):
        page, sections = self.render([host('live', [bot('retry', health='last run failed: temporary error')])])
        self.assertIn('1</b> idle', page)
        self.assertIn('@retry', sections['fleet-idle'])
        self.assertIn('Last run failed · can retry', sections['fleet-idle'])
        self.assertIn('0</b> need attention', page)
        self.assertIn('last run failed: temporary error', page)

    def test_stopped_worker_with_a_problem_has_consistent_blocked_counts(self):
        page, sections = self.render([host('live', [bot('broken', 'stopped', problem='failed to stop cleanly')])])
        self.assertIn('1</b> need attention', page)
        self.assertIn('0 available · 1 blocked · 0 launchers alive', sections['box-live'])
        self.assertIn('failed to stop cleanly', sections['fleet-attention'])

    def test_credentials_and_empty_machine_states(self):
        page, sections = self.render([host('empty', [], credentials=[
            dict(name='claude', state='missing', detail='Run claude /login'),
        ])])
        self.assertIn('Run claude /login', sections['fleet-attention'])
        self.assertIn('No live workers', sections['box-empty'])
        self.assertIn('No workers are available', sections['fleet-idle'])
        self.assertIn('No box has reported yet', self.render([])[0])

    def test_untrusted_names_and_health_are_escaped(self):
        page, _ = self.render([host('<machine>', [bot('<worker>', health='out of usage: <script>bad()</script>')])])
        self.assertNotIn('<script>bad()</script>', page)
        self.assertIn('&lt;script&gt;bad()&lt;/script&gt;', page)
        self.assertIn('&lt;machine&gt;', page)

    def test_add_bot_form_lists_reporting_boxes_harnesses_and_projects(self):
        page = web.fleet_page('board', [host('live', [bot('ready')]),
                                        host('dark', [], reporting=False)],
                              projects=['falco', 'baby-falco'])
        self.assertIn('+ Add a bot', page)
        self.assertIn('class="newbot-top"', page)
        self.assertIn('<option value="live">live</option>', page)
        self.assertNotIn('<option value="dark">dark</option>', page)
        for harness in ('claude', 'codex', 'opencode', 'copilot'):
            self.assertIn('>' + harness + '<', page)
        self.assertIn('No project (shared pool)', page)
        self.assertIn('<option value="falco">falco</option>', page)
        self.assertIn('<option value="baby-falco">baby-falco</option>', page)

    def test_add_bot_form_absent_without_a_reporting_box(self):
        page = web.fleet_page('board', [host('dark', [], reporting=False)], projects=['falco'])
        self.assertNotIn('class="newbot-top"', page)
        self.assertIn('a box must be reporting', page)

    def test_add_machine_section_is_present_even_without_hosts(self):
        page, sections = self.render([])
        setup = sections['fleet-add-machine']
        self.assertIn('Before you start', setup)
        self.assertIn('Python 3', setup)
        self.assertIn('gh auth login', setup)
        self.assertIn('Open a terminal on that machine', setup)
        self.assertIn('id="machine-copy" disabled', page)
        self.assertIn('id="machine-status"', page)
        self.assertIn('location.origin', web.FLEET_JS)
        self.assertIn('KEEPER_OPERATORS=', web.FLEET_JS)
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
