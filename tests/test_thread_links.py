"""Bots name threads by number; a human needs a link. Posting and reading a thread return
the thread's url, and the web page turns "thread 512" into a link.
Run with python -m unittest discover -s tests."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main, web


class ThreadLinks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        r = self.client.post('/api/agents', json={'handle': 'jac', 'kind': 'human'})
        self.jac = {'Authorization': 'Bearer ' + r.json()['token']}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def post(self, **kw):
        r = self.client.post('/api/messages', headers=self.jac, json={'channel': 'general', **kw})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_a_post_and_a_thread_carry_the_thread_url(self):
        root = self.post(body='question')
        tid = root['message']['id']
        self.assertEqual(root['url'], f'http://testserver/t/{tid}')
        reply = self.post(body='answer', reply_to=tid)
        self.assertEqual(reply['url'], f'http://testserver/t/{tid}')    # the thread, not the reply
        got = self.client.get(f'/api/threads/{reply["message"]["id"]}', headers=self.jac).json()
        self.assertEqual(got['url'], f'http://testserver/t/{tid}')

    def test_the_page_links_thread_numbers(self):
        self.assertIn("(thread|message|msg)( #?)(\\d+)", Path(web.__file__).read_text())


if __name__ == '__main__':
    unittest.main()
