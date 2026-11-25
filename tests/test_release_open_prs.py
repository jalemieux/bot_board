"""#932: a release or hand-off notice lists the open pull requests of its thread, and the forge never blocks or changes it.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, fleet, forge, registry, workspaces


class Release(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        db.init(); fleet.init(); workspaces.init(); registry.init()
        self.addCleanup(self.tmp.cleanup)
        self.items, self.status, self.seen = [], 200, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.seen.append(self.path)
                raw = json.dumps(outer.items).encode()
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))
        url = "http://127.0.0.1:%d" % self.server.server_port
        for p in (mock.patch.object(fleet, "forge_url", lambda: url), mock.patch.object(fleet, "forge_token", lambda **kw: "tok")):
            p.start()
            self.addCleanup(p.stop)
        c = db.conn()
        self.ed, _ = db.register_agent("edie"); self.fr, _ = db.register_agent("fran")
        c.execute("INSERT INTO boards (slug, name, kind, workspace_id, created_at) VALUES ('art','art','channel',NULL,'t')")
        bid = c.execute("SELECT id FROM boards WHERE slug='art'").fetchone()[0]
        c.execute("INSERT INTO workspaces (slug, name, purpose, board_id, owner_id, repository, created_at, updated_at)"
                  " VALUES ('w','w','p',?,?,'jac/x','t','t')", (bid, self.ed["id"]))
        c.execute("UPDATE boards SET workspace_id=(SELECT id FROM workspaces) WHERE id=?", (bid,))
        c.commit()
        self.tid = db.post_message("art", self.ed["id"], "build it")["id"]
        db.claim_thread(self.tid, self.ed)

    def last(self):
        return db.conn().execute("SELECT body FROM messages WHERE thread_id=? ORDER BY id DESC LIMIT 1", (self.tid,)).fetchone()[0]

    def release(self, note=""):
        db.release_thread(self.tid, self.ed, None, note)
        return self.last()

    def test_the_line_names_open_pull_requests_by_number_and_title_with_handles_flattened(self):
        self.items = [{"number": 858, "title": "Fix @jac's thing\nsecond line", "pull_request": {}}]
        line = forge.open_line(self.tid)
        self.assertEqual(line, "Open on the forge for this thread: PR #858 (Fix jac's thing second line).")
        self.assertNotIn("@", line)
        self.assertEqual(self.release(line), "Thread worker released; available for pickup. " + line)

    def test_with_nothing_open_the_notice_is_as_it_was(self):
        self.assertEqual(forge.open_line(self.tid), "")
        self.assertEqual(self.release(forge.open_line(self.tid)), "Thread worker released; available for pickup.")

    def test_a_forge_that_is_down_or_a_workspace_without_a_repository_adds_nothing_and_raises_nothing(self):
        self.items = [{"number": 1, "title": "t"}]
        self.status = 500
        self.assertEqual(forge.open_line(self.tid), "")
        with mock.patch.object(fleet, "forge_url", lambda: ""):
            self.assertEqual(forge.open_line(self.tid), "")
        self.status = 200
        db.conn().execute("UPDATE workspaces SET repository=NULL"); db.conn().commit()
        self.seen.clear()
        self.assertEqual(forge.open_line(self.tid), "")
        self.assertEqual(self.seen, [], "no repository, nobody to ask")

    def test_a_hand_to_a_named_bot_carries_the_line(self):
        db.release_thread(self.tid, self.ed, "fran", "Open on the forge for this thread: PR #9 (t).")
        self.assertEqual(self.last(), "Thread handed to @fran by @edie. Open on the forge for this thread: PR #9 (t).")

    def test_a_stopped_bots_hand_off_carries_each_threads_line_and_survives_a_failing_lookup(self):
        by = db.get_agent_by_handle("fran")
        asked = []
        db.hand_off_threads("edie", by, lambda t: asked.append(t) or "Open on the forge for this thread: PR #5 (x).")
        self.assertEqual(asked, [self.tid])
        self.assertTrue(self.last().endswith("available for pickup. Open on the forge for this thread: PR #5 (x)."))
        db.claim_thread(self.tid, self.ed)

        def boom(t):
            raise RuntimeError("down")
        self.assertEqual(db.hand_off_threads("edie", by, boom), 1)
        self.assertTrue(self.last().endswith("when stopping @edie; available for pickup."))

    def test_the_sweeps_release_carries_the_line_too(self):
        from app import progress
        self.items = [{"number": 77, "title": "keep going"}]
        self.assertTrue(progress.release(self.tid, "edie", "stopped"))
        self.assertIn("available for pickup. Last from @edie", self.last())
        self.assertTrue(self.last().endswith("Open on the forge for this thread: PR #77 (keep going)."))

    def test_who_may_release_is_asked_before_the_forge_and_a_forge_failure_stops_the_loop(self):
        stranger, _ = db.register_agent("zed")
        with self.assertRaises(PermissionError):
            db.can_release(self.tid, stranger)
        self.assertTrue(db.can_release(self.tid, self.ed))
        db.release_thread(self.tid, self.ed)
        self.assertFalse(db.can_release(self.tid, stranger), "nothing to release: nothing to ask the forge for")
        self.items = [{"number": 1, "title": "t"}]
        self.status = 500
        src = forge.line_source()
        self.assertEqual((src(self.tid), src(self.tid)), ("", ""))
        self.assertEqual(len(self.seen), 1, "after the first failure the forge is not asked again")

    def test_a_long_list_is_cut_and_a_title_is_cut(self):
        self.items = [{"number": n, "title": "t" * 300} for n in range(1, 13)]
        line = forge.open_line(self.tid)
        self.assertIn("and 2 more.", line)
        self.assertLess(len(line), 1500)


if __name__ == "__main__":
    unittest.main()
