"""Stopping a bot from /fleet with open threads: keep, hand off or retire them (#167).
Run with python -m unittest discover -s tests."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from app import db, main


class Handoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register('op', 'human')
        self.keeper = self.register('keeper-box', 'keeper')
        self.client.post('/api/workspaces', headers=self.human, json={'slug': 'proj', 'name': 'Proj', 'purpose': 'x'})
        self.bot = self.register('tarzan', 'claude-code', 'proj')
        self.threads = [self.post_and_claim(f'task {i}') for i in range(3)]
        # one it finished: completed threads are not open and are not handed off
        self.client.post(f'/api/threads/{self.threads[2]}/complete', headers=self.bot,
                         json={'through': self.threads[2]}).raise_for_status()
        self.report('running proj')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind, workspace=None):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind, 'workspace': workspace})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def post_and_claim(self, body):
        r = self.client.post('/api/messages', headers=self.human, json={'channel': 'proj', 'body': body})
        tid = r.json()['message']['id']
        self.client.post(f'/api/threads/{tid}/worker', headers=self.bot).raise_for_status()
        return tid

    def report(self, status):
        r = self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={'bots': [
            {'handle': 'tarzan', 'harness': 'claude', 'status': status, 'serves': '/pool', 'pool': True}]})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['want']

    def ask(self, expected=200, **body):
        r = self.client.put('/api/fleet/bots/tarzan', headers=self.human, json={'host': 'box', **body})
        self.assertEqual(r.status_code, expected, r.text)

    def worker(self, tid):
        return self.client.get(f'/api/threads/{tid}/worker').json()['worker']

    def fleet_bot(self):
        return self.client.get('/api/fleet').json()['hosts'][0]['bots'][0]

    def test_the_row_counts_open_threads_and_stop_asks_first(self):
        self.assertEqual(self.fleet_bot()['open_threads'], 2)
        page = self.client.get('/fleet').text
        self.assertIn('2 open threads', page)
        self.assertIn('data-open=2', page)
        self.assertIn('id="stop-tarzan" hidden', page)
        self.assertIn('task 1', page)
        self.assertNotIn('task 2', page)     # completed
        self.assertIn('name="threads-tarzan" value="handoff" checked', page)     # hand off is the default (#217)
        self.assertNotIn('value="keep" checked', page)

    def test_handoff_waits_for_the_keeper_to_report_it_stopped(self):
        self.ask(run=False, threads='handoff')
        self.report('running proj, stopping')     # its current run is still going
        self.assertEqual(self.worker(self.threads[0]), 'tarzan')
        self.assertIn('hands off 2 threads once stopped', self.client.get('/fleet').text)
        self.report('stopped')
        self.assertIsNone(self.worker(self.threads[0]))
        self.assertIsNone(self.worker(self.threads[1]))
        self.assertEqual(self.worker(self.threads[2]), 'tarzan')     # completed: left alone
        notice = self.client.get(f'/api/threads/{self.threads[0]}').json()['messages'][-1]
        self.assertEqual(notice['body'],
                         'Thread worker released by @op when stopping @tarzan; available for pickup.')
        self.assertEqual(notice['meta'], {'kind': 'thread_released'})
        self.assertEqual(notice['author'], 'op')
        self.report('stopped')     # applied once
        self.assertEqual(len(self.client.get(f'/api/threads/{self.threads[0]}').json()['messages']), 2)
        self.assertIn('fleet-stopped', self.client.get('/fleet').text)     # handed off, not retired

    def test_keep_leaves_the_threads(self):
        self.ask(run=False, threads='keep')
        self.report('stopped')
        self.assertEqual(self.worker(self.threads[0]), 'tarzan')

    def test_retire_hands_off_and_leaves_the_stopped_list_until_started(self):
        self.ask(run=False, kill=True, threads='retire')
        self.report('stopped')
        self.assertIsNone(self.worker(self.threads[0]))
        self.assertTrue(self.fleet_bot()['retired'])
        self.assertNotIn('fleet-stopped', self.client.get('/fleet').text)
        self.ask(run=True)
        self.assertIsNone(self.fleet_bot()['retired'])

    def test_a_start_before_it_stopped_cancels_the_handoff(self):
        self.ask(run=False, threads='handoff')
        self.ask(run=True)
        self.report('stopped')
        self.assertEqual(self.worker(self.threads[0]), 'tarzan')

    def test_bad_choices_are_refused(self):
        self.ask(run=False, threads='burn', expected=409)
        self.ask(run=True, threads='handoff', expected=409)


if __name__ == '__main__':
    unittest.main()
