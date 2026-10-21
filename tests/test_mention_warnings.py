"""#216: an @mention of an agent that is stopped or away reaches nobody, and the sender is
told. Run with python -m unittest discover -s tests."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main


class MentionWarnings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.jac = self.register('jac', 'human')
        for h in ('finn', 'milo', 'ada', 'old'):
            self.register(h, 'claude-code')
        c = db.conn()
        c.execute("UPDATE agents SET last_seen_at='2026-01-01T00:00:00+00:00' WHERE handle IN ('ada','old')")
        c.execute("UPDATE agents SET last_seen_at=? WHERE handle='milo'", (db.now(),))
        for handle, run in (('finn', 0), ('ada', 1)):     # finn stopped on /fleet; ada wanted running
            c.execute("INSERT INTO fleet_bots (handle, host, harness, run, set_by, set_at) VALUES (?,?,?,?,?,?)",
                      (handle, 'box', 'claude', run, 'jac', db.now()))
        c.commit()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def post(self, body):
        r = self.client.post('/api/messages', headers=self.jac, json={'channel': 'general', 'body': body})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_the_sender_is_told_who_will_not_read_it(self):
        got = self.post('@finn @milo @ada @old @jac please look')
        self.assertEqual([(w['mention'], w['state']) for w in got['warnings']], [('finn', 'stopped'), ('old', 'away')])
        # the fleet's word wins: ada was last seen long ago but is wanted running
        self.assertEqual(got['message']['unreachable'], got['warnings'])
        self.assertNotIn('warnings', self.post('@milo please look'))
        self.assertNotIn('warnings', self.post('nobody tagged'))

    def test_readers_see_it_on_the_message(self):
        mid = self.post('@finn please look')['message']['id']
        r = self.client.get('/api/messages?channel=general&view=compact', headers=self.jac).json()
        [m] = [x for x in r['messages'] if x['id'] == mid]
        self.assertEqual(m['unreachable'][0]['mention'], 'finn')


if __name__ == '__main__':
    unittest.main()
