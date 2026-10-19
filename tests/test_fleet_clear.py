"""Clearing stopped alerts is state-checked, permission-checked and preserves identity."""
import os
import tempfile
import threading
import unittest
from fastapi.testclient import TestClient
from app import db, main, fleet


class ClearAlerts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register('operator', 'human')
        self.other = self.register('other', 'human')
        self.keeper = self.register('keeper-box', 'keeper')
        self.bot = self.register('worker', 'codex')
        self.report()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'): db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        response = self.client.post('/api/agents', json={'handle':handle, 'kind':kind})
        return {'Authorization':'Bearer ' + response.json()['token']}

    def report(self, status='stopped', health='old failure', last_run='2026-10-17T00:00:00Z', problems=None):
        response = self.client.put('/api/fleet/hosts/box', headers=self.keeper,
            json={'bots':[{'handle':'worker', 'harness':'codex', 'status':status, 'health':health, 'last_run':last_run}],
                  'operators':['operator'], 'problems':problems or {}})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def item(self):
        box = self.client.get('/api/fleet').json()['hosts'][0]
        bot = box['bots'][0]
        return {'host':'box', 'handle':'worker', 'key':bot['clear_key']}

    def clear(self, items=None, auth=None):
        return self.client.post('/api/fleet/clear', headers=auth or self.human, json={'items':items or [self.item()]})

    def test_clear_cancels_pending_move_and_repeated_stale_report_without_deleting_state(self):
        identity = self.client.get('/api/agents/worker').json()
        fleet.want('worker', 'box', False, 'codex', 'operator')
        with db.conn() as c:
            c.execute("UPDATE fleet_bots SET move_to='elsewhere', fetch='archive', problem='handover missing' WHERE handle='worker'")
        response = self.clear()
        self.assertEqual(response.status_code, 200, response.text)
        wanted = fleet.wanted('box')[0]
        self.assertFalse(wanted['run'])
        self.assertIsNone(wanted['move_to'])
        self.assertIsNone(wanted['fetch'])
        self.assertEqual(wanted['problem'], '')
        self.report(problems={'worker':'handover missing'})
        bot = self.client.get('/api/fleet').json()['hosts'][0]['bots'][0]
        self.assertEqual(bot['health'], '')
        self.assertEqual(bot['problem'], '')
        self.assertEqual(bot['status'], 'stopped')
        self.assertNotIn('clear_key', bot)
        self.assertEqual(self.client.get('/api/agents/worker').json(), identity)
        self.assertNotIn('id="fleet-attention"', self.client.get('/fleet').text)

    def test_live_unknown_and_new_failure_are_not_silently_cleared(self):
        item = self.item()
        self.report(status='running game')
        self.assertEqual(self.clear([item]).status_code, 409)
        self.report(health='new failure')
        self.assertEqual(self.clear([item]).status_code, 409)
        item = self.item()
        with db.conn() as c:
            c.execute("UPDATE fleet_hosts SET reported_at='2000-01-01T00:00:00Z'")
        self.assertEqual(self.clear([item]).status_code, 409)
        self.report()
        self.assertEqual(self.clear().status_code, 200)
        self.report(last_run='2026-10-18T00:00:00Z')
        self.assertTrue(self.client.get('/api/fleet').json()['hosts'][0]['bots'][0]['health'])

    def test_permissions_group_atomicity_and_new_request(self):
        item = self.item()
        self.assertEqual(self.clear([item], self.other).status_code, 403)
        self.assertEqual(self.clear([item], self.bot).status_code, 403)
        self.assertEqual(self.clear([item, {**item,'handle':'missing'}]).status_code, 409)
        self.assertEqual(self.item(), item)
        self.assertEqual(self.clear([item]).status_code, 200)
        # An explicit new request invalidates the acknowledgement, even with identical error text.
        fleet.want('worker', 'box', False, 'codex', 'operator')
        self.report()
        self.assertIn('key', self.item())

    def test_clear_controls_are_present_for_individual_and_group_alerts(self):
        fleet.want('worker', 'box', False, 'codex', 'operator')
        fleet.wanted('box', {'worker':'old failure'})
        page = self.client.get('/fleet').text
        self.assertIn('>Dismiss</button>', page)
        self.assertIn('/api/fleet/clear', page)
        self.report(status='running game')
        self.assertNotIn('class="clear-alert"', self.client.get('/fleet').text)


if __name__ == '__main__':
    unittest.main()
