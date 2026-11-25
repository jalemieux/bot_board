"""#931: a thread is not completed while an issue or pull request labelled thread:<id> is open on its workspace's repository.
A local server stands in for Forgejo. Run with python -m unittest discover -s tests."""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, fleet, forge, workspaces


class Stand(unittest.TestCase):
    def setUp(self):
        self.seen, self.items, self.status = [], [], 200
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.seen.append((self.path, self.headers.get("Authorization")))
                raw = json.dumps(outer.items).encode()
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))
        url = "http://127.0.0.1:%d" % self.server.server_port
        for p in (mock.patch.object(fleet, "forge_url", lambda: url),
                  mock.patch.object(fleet, "forge_token", lambda **kw: "readtok" if kw.get("read") else "wtok")):
            p.start()
            self.addCleanup(p.stop)

    def test_it_asks_for_the_open_items_carrying_the_thread_label_with_the_read_token(self):
        self.items = [{"number": 858, "title": "A PR", "pull_request": {"merged": False}}, {"number": 859, "title": "An issue"}]
        got = forge.open_for_thread("jac/bot_board", 21123)
        self.assertEqual(got, [{"number": 858, "title": "A PR", "pull": True}, {"number": 859, "title": "An issue", "pull": False}])
        path, auth = self.seen[0]
        u = urlparse(path)
        self.assertEqual(u.path, "/api/v1/repos/jac/bot_board/issues")
        self.assertEqual(parse_qs(u.query)["labels"], ["thread:21123"])
        self.assertEqual(parse_qs(u.query)["state"], ["open"])
        self.assertEqual(auth, "token readtok")

    def test_nothing_open_is_an_empty_list(self):
        self.assertEqual(forge.open_for_thread("jac/bot_board", 5), [])

    def test_a_forge_that_errors_raises_for_the_caller_to_warn(self):
        self.status = 500
        with self.assertRaises(forge.ForgeError):
            forge.open_for_thread("jac/bot_board", 5)

    def test_a_crafted_thread_or_repository_never_reaches_the_forge(self):
        for bad in (0, -1, True, "1&x=2", None):
            with self.assertRaises(forge.ForgeError, msg=repr(bad)):
                forge.open_for_thread("jac/bot_board", bad)
        with self.assertRaises(forge.ForgeError):
            forge.open_for_thread("../x", 1)
        self.assertEqual(self.seen, [])


class Board(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        db.init(); fleet.init(); workspaces.init()
        self.addCleanup(self.tmp.cleanup)

    def test_the_repository_of_a_thread_is_its_workspaces_or_empty(self):
        c = db.conn()
        c.execute("INSERT INTO agents (handle, token_hash, kind, created_at) VALUES ('x','h','agent','t')")
        cols = "(slug, name, kind, workspace_id, created_at)"
        c.execute("INSERT INTO boards " + cols + " VALUES ('a','a','channel',NULL,'t')")
        c.execute("INSERT INTO boards " + cols + " VALUES ('b','b','channel',NULL,'t')")
        ids = [r[0] for r in c.execute("SELECT id FROM boards ORDER BY id")]
        c.execute("INSERT INTO workspaces (slug, name, purpose, board_id, owner_id, repository, created_at, updated_at)"
                  " VALUES ('w','w','p',?,1,'jac/x','t','t')", (ids[0],))
        c.execute("UPDATE boards SET workspace_id=(SELECT id FROM workspaces) WHERE id=?", (ids[0],))
        for i, b in enumerate(ids):
            c.execute("INSERT INTO messages (id, board_id, agent_id, body, thread_id, created_at) VALUES (?,?,1,'m',?,'t')", (100 + i, b, 100 + i))
        c.commit()
        self.assertEqual(workspaces.thread_repository(100), "jac/x")
        self.assertEqual(workspaces.thread_repository(101), "")
        self.assertEqual(workspaces.thread_repository(999), "")


if __name__ == "__main__":
    unittest.main()
