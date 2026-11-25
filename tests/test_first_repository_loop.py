"""#841: the keeper's first repository is made by a loop of the board, not by a thread the route starts. The report leaves a row and
returns at once; the next pass asks `forge.ensure_repository` for it, once, as the workspace's owner. Run with
python -m unittest discover -s tests."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, forge, main, workspaces


class FirstRepositoryLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        r = self.client.post("/api/agents", json={"handle": "jac", "kind": "human"})
        self.jac = {"Authorization": "Bearer " + r.json()["token"]}
        r = self.client.post("/api/agents", json={"handle": "keeper-box", "kind": "keeper"})
        self.keeper = {"Authorization": "Bearer " + r.json()["token"]}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.tmp.cleanup()

    def wanted(self):
        return bool(db.conn().execute("SELECT 1 FROM registry_meta WHERE key=?", (forge.WANTED,)).fetchone())

    def report(self):
        r = self.client.put("/api/fleet/hosts/box", headers=self.keeper, json={
            "bots": [], "operators": ["jac"], "default_bot": {"harness": "claude", "handle": "ada"}})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_the_report_returns_at_once_and_the_next_pass_makes_the_repository_once(self):
        called = []
        with mock.patch.object(forge, "ensure_repository", lambda slug, actor, timeout=None: called.append((slug, actor["handle"])) or {"repository": "fleet/general", "reason": ""}):
            got = self.report()
            self.assertTrue(got["default_bot"]["created"])
            self.assertEqual(called, [], "the route does not ask the forge: it leaves a row")
            self.assertTrue(self.wanted())
            self.assertEqual(forge.make_wanted(), "fleet/general")
            self.assertEqual(called, [("general", "jac")], "once, as the workspace's own owner and not a handle the report names")
            self.assertFalse(self.wanted())
            self.assertIsNone(forge.make_wanted(), "run twice: nothing wanted, nothing asked")
            self.assertEqual(len(called), 1)

    def test_it_is_safe_when_the_workspace_already_has_a_repository(self):
        ws = workspaces.find(workspaces.FIRST_RUN_WORKSPACE)
        self.assertTrue(db.get_agent_by_id(ws["owner_id"]))
        forge.want_first_repository(); forge.want_first_repository()      # one row, however often it is asked
        with mock.patch.object(workspaces, "repository", lambda w: "fleet/general"):
            self.assertEqual(forge.make_wanted(), "fleet/general")
        self.assertFalse(self.wanted())

    def test_a_forge_that_cannot_make_it_is_said_and_not_retried(self):
        called = []
        with mock.patch.object(forge, "ensure_repository", lambda *a, **k: called.append(1) or {"repository": "", "reason": "the forge did not answer"}):
            forge.want_first_repository()
            self.assertIsNone(forge.make_wanted())
            self.assertFalse(self.wanted(), "the Team tab offers Create repository")
            forge.make_wanted()
            self.assertEqual(len(called), 1)

    def test_a_row_written_during_the_pass_is_not_lost(self):
        def slow(slug, actor, timeout=None):
            with db._write_lock:     # another report arrives while the forge is being asked
                db.conn().execute("UPDATE registry_meta SET value='later' WHERE key=?", (forge.WANTED,)); db.conn().commit()
            return {"repository": "fleet/general", "reason": ""}
        forge.want_first_repository()
        with mock.patch.object(forge, "ensure_repository", slow):
            forge.make_wanted()
        self.assertTrue(self.wanted(), "the newer row waits for the next pass")

    def test_an_error_in_the_pass_is_caught_and_the_row_still_clears(self):
        with mock.patch.object(forge, "ensure_repository", side_effect=RuntimeError("boom")):
            forge.want_first_repository()
            self.assertIsNone(forge.make_wanted())
        self.assertFalse(self.wanted())

    def test_no_route_starts_a_thread_for_it(self):
        src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
        self.assertNotIn("def _first_repository", src)
        self.assertNotIn("threading.Thread(target=_first", src)
        self.assertIn("forge.want_first_repository()", src)
        self.assertIn("forge.start_wanted(", src, "the loop is started in _startup, beside the progress sweep")


if __name__ == "__main__":
    unittest.main()
