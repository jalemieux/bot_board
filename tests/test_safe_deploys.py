"""#243: a deploy cuts nothing off. A message POST retried with the same client_key is posted
once; open long-polls answer as soon as the board is asked to stop; the routines runner keys
what it posts. Run with python -m unittest discover -s tests."""
import asyncio
import importlib.machinery
import importlib.util
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient     # noqa: E402
from app import db, main     # noqa: E402


class ClientKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.ann = self.register('ann')
        self.bob = self.register('bob')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': 'claude-code'})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def post(self, who, **body):
        r = self.client.post('/api/messages', headers=who, json={'channel': 'general', 'body': 'hi', **body})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def bodies(self):
        return [m['id'] for m in self.client.get('/api/messages?channel=general&limit=100').json()['messages']]

    def test_a_retry_with_the_same_key_posts_once(self):
        first = self.post(self.ann, client_key='k1')
        again = self.post(self.ann, client_key='k1', body='hi (retried)')
        self.assertNotIn('replayed', first)
        self.assertTrue(again['replayed'])
        self.assertEqual(again['message']['id'], first['message']['id'])
        self.assertEqual(again['message']['body'], 'hi')
        self.assertEqual(self.bodies(), [first['message']['id']])

    def test_keys_are_per_poster_and_optional(self):
        a, b = self.post(self.ann, client_key='k'), self.post(self.bob, client_key='k')
        c, d = self.post(self.ann), self.post(self.ann)
        self.assertEqual(len({m['message']['id'] for m in (a, b, c, d)}), 4)

    def test_a_key_whose_message_is_gone_posts_again(self):
        first = self.post(self.ann, client_key='k')
        db.conn().execute('DELETE FROM messages WHERE id=?', (first['message']['id'],))
        db.conn().commit()
        again = self.post(self.ann, client_key='k')
        self.assertNotIn('replayed', again)
        self.assertNotEqual(again['message']['id'], first['message']['id'])


class LongPolls(unittest.TestCase):
    def test_open_long_polls_answer_when_the_board_is_asked_to_stop(self):
        async def run():
            main._new_message, main._stopping = asyncio.Condition(), False     # this loop's
            started = time.monotonic()
            poll = asyncio.ensure_future(main._wait_for(lambda: [], 30))
            await asyncio.sleep(0.1)
            self.assertFalse(poll.done())
            await main._answer_long_polls()
            self.assertEqual(await asyncio.wait_for(poll, 2), [])
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(await main._wait_for(lambda: [], 30), [], "a new one answers at once")
        saved = main._new_message
        try:
            asyncio.run(run())
        finally:
            main._new_message, main._stopping = saved, False


class Runner(unittest.TestCase):
    def test_the_runner_keys_what_it_posts_by_content_and_ten_minutes(self):
        loader = importlib.machinery.SourceFileLoader("routines_k", str(ROOT / "bin" / "routines"))
        cli = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        saved = os.environ.get("BOT_CONFIG")
        os.environ["BOT_CONFIG"] = os.devnull     # not this box's config
        try:
            loader.exec_module(cli)
        finally:
            if saved is None: os.environ.pop("BOT_CONFIG")
            else: os.environ["BOT_CONFIG"] = saved
        sent = []
        cli.call = lambda method, path, body=None, token=None: sent.append(body) or {"message": {"id": 1}}
        board = cli.Board.__new__(cli.Board)
        board.token = "t"
        board.post("game", "announce #3", meta={"routine": 1})
        board.post("game", "announce #3", meta={"routine": 1})
        board.post("game", "announce #4", meta={"routine": 1})
        keys = [b["client_key"] for b in sent]
        self.assertEqual(keys[0], keys[1])
        self.assertNotEqual(keys[0], keys[2])
        self.assertTrue(keys[0].endswith(f":{int(time.time() // 600)}"))


class Launcher(unittest.TestCase):
    def test_bin_bot_notices_retry_with_a_key(self):
        text = (ROOT / "bin" / "bot").read_text()
        say = text[text.index("say() {"):text.index("\n}\n", text.index("say() {"))]
        self.assertIn('"client_key":uuid.uuid4().hex', say)
        self.assertIn("--retry-connrefused", say)


if __name__ == "__main__":
    unittest.main()
