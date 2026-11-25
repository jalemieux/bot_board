"""#889: the Machines page is an empty shell; its script reads /api/fleet and draws the table. The script runs under node against what the API
answers (tests/machinespage.py). What the table says for each kind of box is in test_fleet_page.py. Skipped without node."""
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import machinespage
from app import web

H = dict(name='box', os='Linux', reporting=True, reported_at=None, load=2, cores=4, mem_total_mb=1000, mem_free_mb=250, credentials=[], bots=[])


def host(name='box', **extra):
    return {**H, 'name': name, **extra}


@unittest.skipUnless(shutil.which('node'), 'node is needed')
class MachinesScript(unittest.TestCase):
    def test_it_reads_the_api_with_the_readers_token_and_says_it_has_drawn(self):
        out = machinespage.machines([host(), host('b2')], me={'handle': 'jac', 'token': 'T0K'})
        self.assertEqual(out['log']['fetched'], [['/api/fleet', {'Authorization': 'Bearer T0K'}]])
        self.assertEqual(out['log']['sorted'], 1, 'the table is wired for sorting once it is drawn')
        self.assertEqual(out['log']['events'], ['machines-drawn'], 'the Mac card is placed when it is told')
        self.assertEqual(out['count'], '2 machines')
        self.assertEqual(machinespage.machines([host()])['log']['fetched'], [['/api/fleet', {}]], 'an unsigned reader sends no token')

    def test_a_link_to_a_row_lands_on_it_once_the_row_is_drawn(self):
        self.assertEqual(machinespage.machines([host()], hash='#box-box')['log']['targets'], 1)
        self.assertEqual(machinespage.machines([host()])['log']['targets'], 0)

    def test_a_read_that_fails_says_so_and_draws_no_table(self):
        out = machinespage.machines([host()], ok=False)
        self.assertEqual(out['text'], 'The machines could not be read from the board.')
        self.assertEqual((out['html'], out['log']['events']), ('', []))

    def test_a_page_without_the_slot_reads_nothing(self):
        self.assertEqual(machinespage.machines([host()], has_slot=False)['log']['fetched'], [])

    def test_the_sort_cells_hold_the_values_the_columns_sort_by(self):
        html = machinespage.machines([host(load=9, cores=8, bots=[{'handle': 'a', 'status': 'waiting'}, {'handle': 'b', 'status': 'stopped'}],
                                           credentials=[{'name': 'c', 'state': 'missing'}, {'name': 'd', 'state': 'ok'}]),
                                      host('b2', load=None, mem_free_mb=None, reporting=False)])['html']
        first, second = html.split('<tr id="box-b2">')
        self.assertIn('data-label="load" data-sort="1.125"', first)
        self.assertIn('data-label="memory" data-sort="0.75"', first)
        self.assertIn('data-label="bots" class="nw" data-sort="1"', first, 'a stopped bot is not up')
        self.assertIn('data-label="logins" data-sort="1"', first, 'how many of its logins are in trouble')
        self.assertIn('data-label="status" class="nw" data-sort="0"', second)
        self.assertIn('data-label="load" data-sort="-1"', second)
        self.assertIn('data-label="memory" data-sort="-1"', second)

    def test_a_box_offers_to_drain_only_when_another_box_is_reporting_and_pool_bots_are_up(self):
        pool = [{'handle': 'p', 'status': 'waiting', 'pool': True}]
        self.assertIn('class="drain" data-host="box" data-n="1"', machinespage.machines([host(bots=pool), host('other')])['html'])
        self.assertNotIn('class="drain"', machinespage.machines([host(bots=pool)])['html'], 'nowhere to move them')
        self.assertNotIn('class="drain"', machinespage.machines([host(bots=pool), host('other', reporting=False)])['html'])
        self.assertNotIn('class="drain"', machinespage.machines([host(bots=[{**pool[0], 'move_to': 'other'}]), host('other')])['html'])
        self.assertNotIn('class="drain"', machinespage.machines([host(bots=[{'handle': 'p', 'status': 'stopped', 'pool': True}]), host('other')])['html'])

    def test_remove_names_the_bots_and_says_whether_the_box_is_reporting(self):
        html = machinespage.machines([host(bots=[{'handle': 'a', 'status': 'waiting'}, {'handle': 'b', 'status': 'stopped'}])])['html']
        self.assertIn('class="remove-box" data-host="box" data-bots="a b" data-reporting="1"', html)
        self.assertIn('data-reporting="0"', machinespage.machines([host(reporting=False)])['html'])

    def test_a_machine_that_is_not_reporting_says_how_long_ago(self):
        html = machinespage.machines([host(reporting=False, reported_at='2020-01-01T00:00:00+00:00')])['html']
        self.assertIn('<span class="pip bad"></span>not reporting · ', html)
        self.assertRegex(html, r'not reporting · \d+d ago')

    def test_the_route_sends_a_shell_with_no_machine_in_it(self):
        page = web.fleet_page('board', page='machines')
        self.assertIn('<div id="machines-slot"><p class="sub">Loading', page)
        self.assertNotIn('<table class="bots machines"', page.split('<script>')[0])
        self.assertIn("fetch('/api/fleet'", page)


if __name__ == '__main__':
    unittest.main()
