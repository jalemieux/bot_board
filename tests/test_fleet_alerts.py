"""#887 (part of #845): the Machines page's alerts and host banner are built by the page script from /api/fleet.
The model is a pure function (`fleetAlertsModel`), run here under node. Skipped without node."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, fleet, web

NODE = shutil.which('node')


def bot(handle, status='waiting', **extra):
    return dict(handle=handle, status=status, harness='codex', **extra)


def host(name, bots, reporting=True, **extra):
    return dict(name=name, bots=bots, reporting=reporting, reported_at=db.now(), keeper='keeper-' + name, cores=8, load=0.3,
                mem_total_mb=16000, mem_free_mb=12000, **extra)


def model(hosts, token=None):
    # /api/fleet gives each bot its `state` (fleet.hosts); the fixtures get it the same way
    hosts = [{**h, 'bots': [{**b, 'state': fleet.bot_state(b, h['reporting'])} for b in h['bots']]} for h in hosts]
    src = web.FLEET_ALERTS_JS + '\nconst a = JSON.parse(require("fs").readFileSync(0, "utf8"));' \
        '\nprocess.stdout.write(JSON.stringify(fleetAlertsModel(a.hosts, a.token)));'
    out = subprocess.run([NODE, '-e', src], input=json.dumps({'hosts': hosts, 'token': token}),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def text(alert):
    return ' '.join(p if isinstance(p, str) else p.get('code') or p.get('text') or p.get('time') or '' for p in alert['text'])


@unittest.skipUnless(NODE, 'node is not installed')
class FleetAlerts(unittest.TestCase):
    def test_usage_alert_is_grouped_and_says_nothing_needs_doing(self):
        reason = 'out of codex usage: weekly limit; resets Monday'
        m = model([host('live', [bot('one', health=reason),
                                 bot('two', 'stopped', health=reason, want='running', set_by='op', set_at=db.now())])])
        self.assertEqual(len(m['alerts']), 1)
        a = m['alerts'][0]
        self.assertIn('codex usage limit reached', a['title'])
        self.assertIn('once the limit resets', text(a))
        self.assertEqual({b['handle'] for b in a['bots']}, {'one', 'two'})
        self.assertIn('Stop them', [b['label'] for b in a['buttons']])

    def test_a_drained_machine_with_a_retired_bot_has_no_alert(self):
        m = model([host('live', [bot('ready'), bot('retired', 'stopped', health='out of codex usage: old limit',
                                                   want='stopped', set_by='op', set_at=db.now())]), host('other', [])])
        self.assertEqual(m['alerts'], [])

    def test_requested_start_and_failure_stay_in_view_with_a_fix(self):
        m = model([host('live', [bot('starting', 'stopped', want='running', set_by='op', set_at=db.now()),
                                 bot('broken', 'stopped', want='running', problem='codex not installed',
                                     set_by='op', set_at=db.now())])])
        a = next(x for x in m['alerts'] if 'Could not start 1 bot' in x['title'])
        self.assertIn('codex not installed', text(a))
        labels = [b['label'] for b in a['buttons']]
        self.assertIn('Try again', labels)
        self.assertIn('Leave stopped', labels)

    def test_credentials_and_a_held_bot(self):
        m = model([host('empty', [], credentials=[dict(name='claude', state='missing', detail='Run claude /login')])])
        self.assertIn('Run claude /login', ' '.join(text(a) for a in m['alerts']))
        held = model([host('box', [bot('dex', health='logged out of codex')],
                           credentials=[dict(name='codex', state='missing', detail='not logged in')])])
        self.assertIn('codex missing on box', ' '.join(a['title'] + text(a) for a in held['alerts']))
        self.assertEqual(len(held['alerts']), 1, 'the bots a bad credential holds are not one more alert each')

    def test_a_rejected_credential_says_how_to_replace_it_and_how_many_bots_it_holds(self):
        m = model([host('box', [], credentials=[dict(name='claude', state='rejected', held=1,
                                                      detail='token rejected: API Error: 401 OAuth access token is invalid.')])])
        a = m['alerts'][0]
        self.assertEqual(a['title'], 'claude rejected on box')
        self.assertIn({'code': 'claude setup-token'}, a['text'])
        held = model([host('box', [], credentials=[dict(name='claude', state='missing', held=2, detail='not logged in')])])
        self.assertEqual(held['alerts'][0]['title'], 'claude missing on box')
        self.assertIn('not logged in. It is holding 2 bots.', text(held['alerts'][0]))

    def test_a_host_that_stopped_reporting_is_an_alert(self):
        m = model([host('offline', [bot('ghost')], reporting=False)])
        self.assertIn('offline stopped reporting', ' '.join(a['title'] + text(a) for a in m['alerts']))

    def test_health_timestamps_are_times_and_diagnostics_are_plain_strings(self):
        health = 'out of codex usage since 2026-10-17T06:57:20Z: <script>bad</script>'
        m = model([host('live', [bot('limited', health=health)])])
        parts = [p for a in m['alerts'] for p in a['text']]
        self.assertIn({'time': '2026-10-17T06:57:20Z'}, parts)
        self.assertIn('<script>bad</script>', ' '.join(p for p in parts if isinstance(p, str)) + ' '.join(
            str(p.get('text', '')) for p in parts if isinstance(p, dict)),
            'the model keeps the raw text; the renderer sets it as text, never as HTML')
        self.assertNotIn('2026-99-99', json.dumps([{'time': p['time']} for p in parts if isinstance(p, dict) and 'time' in p]))
        bad = model([host('live', [bot('limited', health='out of codex usage 2026-99-99T06:57:20Z x')])])
        self.assertNotIn({'time': '2026-99-99T06:57:20Z'}, [p for a in bad['alerts'] for p in a['text']])

    def test_the_claude_token_alert(self):
        for left, when in ((10, 'expires in 10 days'), (1, 'expires today'), (0, 'has expired'), (-3, 'has expired')):
            with self.subTest(left=left):
                m = model([], {'set_at': '2025-11-25T00:00:00+00:00', 'days_left': left, 'renew_by': '2026-11-25'})
                self.assertEqual([a['title'] for a in m['alerts']], ["The fleet's claude token " + when])
                self.assertIn('2025-11-25', text(m['alerts'][0]))
        self.assertEqual(model([], None)['alerts'], [])

    def test_the_banner_counts_host_bots_even_after_a_move_back(self):
        m = model([host('mac', [bot('a', host_flag=True), bot('b')])])
        self.assertIsNotNone(m['banner'])
        self.assertEqual(m['banner']['n'], 1)
        self.assertIsNone(model([host('mac', [bot('b')])])['banner'])

    def test_the_banner_says_what_a_host_bot_is(self):
        mira = dict(host_flag=True, runmode='host', host_reason='Godot captures', want='running')
        m = model([host('jacs-m5', [bot('mira', **mira), bot('edie', runmode='native')]), host('box', [bot('zed', runmode='native')])])
        self.assertEqual(m['banner']['items'], [{'handle': 'mira', 'box': 'jacs-m5', 'reason': 'Godot captures'}])
        moved = dict(want='running', runmode='host', host_flag=False)
        self.assertEqual(model([host('jacs-m5', [bot('mira', **moved)])])['banner']['n'], 1,
                         'a bot moved back but still running on the host stays in the banner')

    def test_the_page_renders_the_script_and_a_placeholder_not_the_alerts(self):
        page = web.fleet_page('board', [host('live', [bot('one', health='out of codex usage: x')])])
        self.assertIn('<div id="fleet-alerts"></div>', page)
        self.assertNotIn('codex usage limit reached', page.split('<script')[0])


if __name__ == '__main__':
    unittest.main()
