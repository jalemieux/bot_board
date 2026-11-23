"""#232: the header's chip counts the bots the fleet reports running, not every recent token."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, fleet, main, web, workspaces


class RunningChip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.register('jac', 'human')
        self.keeper = self.register('keeper-box', 'keeper')
        self.register('routines', 'cron')
        for h in ('ann', 'bob', 'cal', 'dot', 'eve'):
            self.register(h, 'claude-code')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def seen(self, handles, at):
        marks = ','.join('?' * len(handles))
        db.conn().execute(f'UPDATE agents SET last_seen_at=? WHERE handle IN ({marks})', (at, *handles))
        db.conn().commit()

    def report(self):
        r = self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={'bots': [
            {'handle': 'ann', 'harness': 'claude', 'status': 'waiting'},
            {'handle': 'bob', 'harness': 'claude', 'status': 'running 7756'},
            {'handle': 'cal', 'harness': 'claude', 'status': 'stopped'},
            {'handle': 'dot', 'harness': 'claude', 'status': 'waiting', 'health': 'logged out since 00:00'},
        ]})
        self.assertEqual(r.status_code, 200, r.text)

    def test_counts_running_bots_not_keepers_runner_humans_or_stray_tokens(self):
        self.report()
        # everyone made a request just now: cal's token was used by hand, though its launcher is stopped
        self.seen(['jac', 'keeper-box', 'routines', 'ann', 'bob', 'cal', 'dot'], db.now())
        self.assertEqual(db.stats()['active'], 7)       # presence itself is unchanged
        self.assertEqual(fleet.running(), {'bots': 2, 'working': 1, 'keepers': 1, 'runners': 1, 'humans': 1})

    def test_a_bot_no_keeper_reports_counts_by_presence(self):
        self.report()
        self.seen(['eve'], db.now())
        self.assertEqual(fleet.running()['bots'], 3)
        self.seen(['eve'], '2026-01-01T00:00:00+00:00')
        self.assertEqual(fleet.running()['bots'], 2)

    def test_header_shows_running_with_breakdown(self):
        self.report()
        self.seen(['jac', 'keeper-box', 'routines'], db.now())
        page = self.client.get('/').text
        self.assertIn('<a href="/workspaces" class="tab"', page)  # the count counts bots, and the bots live in workspaces (thread 17902)
        self.assertIn('<span id="nag">2 bots running, 1 waiting for a login</span>', page)
        self.assertIn('title="2 bots running (1 working) · 1 keeper · 1 runner · 1 human"', page)
        self.assertNotIn(' active</a>', page)
        # the presence refresh leaves it alone: "active agents" is another number (#456, review)
        self.assertNotIn("$('#nag').textContent", web.APP_JS)
        self.assertEqual(web.running_chip(None), '')

    def chip(self):
        return web.running_chip(fleet.running())

    def ask(self, handle):
        try:
            workspaces.find('proj')
        except KeyError:
            workspaces.create({'slug': 'proj', 'name': 'proj', 'purpose': 'x'}, db.get_agent_by_handle('jac'))
        self.assertIsNone(fleet.want(handle, 'box', True, 'claude', 'jac', workspace='proj'))

    def test_a_wanted_bot_that_has_not_started_reads_starting_not_zero(self):
        # #776: the keeper reported a bot stopped, and a human asked for it to run: it is on its way
        self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={'bots': [
            {'handle': 'ann', 'harness': 'claude', 'status': 'stopped'}]})
        self.ask('ann')
        self.assertIn('<span id="nag">1 bot starting</span>', self.chip())
        self.assertIn('1 starting', self.chip())

    def test_a_running_bot_beside_a_starting_one_says_both(self):
        self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={'bots': [
            {'handle': 'ann', 'harness': 'claude', 'status': 'waiting'},
            {'handle': 'bob', 'harness': 'claude', 'status': 'stopped'}]})
        self.ask('bob')
        self.assertIn('<span id="nag">1 bot running, 1 starting</span>', self.chip())

    def test_a_bot_with_no_login_reads_waiting_for_a_login(self):
        self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={'bots': [
            {'handle': 'dot', 'harness': 'claude', 'status': 'waiting', 'health': 'logged out since 00:00'}]})
        self.assertIn('<span id="nag">1 bot waiting for a login</span>', self.chip())

    def test_zero_only_when_no_bot_is_wanted_and_all_running_reads_as_before(self):
        self.assertIn('<span id="nag">0 bots running</span>', self.chip())
        self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={'bots': [
            {'handle': 'ann', 'harness': 'claude', 'status': 'waiting'},
            {'handle': 'bob', 'harness': 'claude', 'status': 'running 7756'}]})
        self.assertIn('<span id="nag">2 bots running</span>', self.chip())

    def test_the_top_bar_has_tabs_not_a_manage_menu(self):
        # thread 9918, #644: Board and the board-wide pages as tabs; the current page is marked
        for path, current in (('/', '/'), ('/machines', '/machines'), ('/configuration', '/configuration'), ('/roles', '/roles'),
                              ('/skills', '/skills'), ('/routines', '/routines'), ('/workspaces', '/workspaces')):
            page = self.client.get(path).text
            self.assertNotIn('id="manage"', page, path)
            self.assertNotIn('id="navboard"', page, path)
            self.assertNotIn('<a href="/fleet" class="tab"', page, path)     # the bots are a tab of each workspace
            for href in ('/', '/workspaces', '/machines', '/configuration', '/roles', '/skills', '/routines'):
                cur = ' aria-current="page"' if href == current else ''
                self.assertRegex(page, f'<a href="{href}" class="tab" title="[^"]*"{cur}>', (path, href))

    def test_the_old_fleet_page_is_now_one_page_per_subject(self):
        # #644: Machines, Configuration, Roles and Skills each show only their own panes; Fleet is not a tab any more
        panes = {'/machines': 'tab-machines tab-settings', '/configuration': 'tab-settings',
                 '/roles': 'tab-roles', '/skills': 'tab-skills'}
        for path, shown in panes.items():
            page = self.client.get(path).text
            self.assertIn(f'data-panes="{shown}"', page, path)
            self.assertNotIn('class="fleet-tabs"', page, path)
            self.assertNotIn('>Fleet</a>', page.split('</header>')[0], path)
        self.assertIn('<h1>Machines</h1>', self.client.get('/machines').text)
        self.assertNotIn('id="addbot"', self.client.get('/roles').text, 'adding a bot belongs to the bots page')

    def test_the_phone_menu_names_the_current_page_and_carries_the_theme(self):
        # #647: at 700px and under the tab row is one button with the page's name, opening a sheet
        for path, name in (('/', 'Board'), ('/machines', 'Machines'), ('/skills', 'Skills'), ('/workspaces', 'Workspaces')):
            page = self.client.get(path).text
            self.assertIn(f'<span class="short">{name}</span>', page, path)
            self.assertIn('id="themerow"', page, path)
        css = web.TOPNAV_RESPONSIVE_CSS
        for needle in ('.topnav .links.open{position:fixed', '.topnav .links.open::before', '#themebtn{display:none}', 'min-height:48px'):
            self.assertIn(needle, css)


if __name__ == '__main__':
    unittest.main()
