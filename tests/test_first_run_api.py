"""#774: the first-run guide's saved state and the facts it shows. A missing key means no guide; a fresh board's first
human gets `pending`; skip and done are saved; a bot is refused; the facts hold no secret. Run with python -m unittest
discover -s tests."""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, fleet, main


class FirstRunApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.jac = self.register("jac", "human")       # the board's first human: the Admin workspace is made for them

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post("/api/agents", json={"handle": handle, "kind": kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": "Bearer " + r.json()["token"]}

    def get(self, who=None):
        return self.client.get("/api/first-run", headers=who or self.jac)

    def test_a_fresh_boards_first_sign_in_makes_it_pending_and_only_that_one(self):
        self.assertEqual(self.get().json()["state"], "pending")
        second = self.register("sam", "human")
        self.assertIsNone(self.get(second).json()["state"], "a later human is not shown it: only the first sign-in made the Admin workspace")

    def test_an_old_board_with_no_key_has_no_guide(self):
        with db._write_lock:
            db.conn().execute("DELETE FROM registry_meta WHERE key LIKE 'first_run:%'"); db.conn().commit()
        got = self.get().json()
        self.assertIsNone(got["state"])
        self.assertIn("steps", got)       # the facts are still there: the page decides nothing without a state

    def test_skip_done_and_reopen_are_saved_and_survive_a_restart(self):
        for value in ("skipped", "skipped", "done", "pending"):      # idempotent, and the link reopens it
            r = self.client.put("/api/first-run", headers=self.jac, json={"state": value})
            self.assertEqual((r.status_code, r.json()), (200, {"state": value}), r.text)
            self.assertEqual(self.get().json()["state"], value)
        self.client.put("/api/first-run", headers=self.jac, json={"state": "done"})
        db._local = threading.local()             # a new connection, as after a restart
        self.assertEqual(self.get().json()["state"], "done")
        self.assertEqual(self.client.put("/api/first-run", headers=self.jac, json={"state": "bogus"}).status_code, 422)

    def test_a_bot_is_refused(self):
        bot = self.register("ada", "claude-code")
        self.assertEqual(self.get(bot).status_code, 403)
        self.assertEqual(self.client.put("/api/first-run", headers=bot, json={"state": "done"}).status_code, 403)
        self.assertEqual(self.client.get("/api/first-run").status_code, 401)

    def test_the_facts_say_what_the_board_knows_and_hold_no_secret(self):
        steps = self.get().json()["steps"]
        self.assertEqual(steps["board"]["handle"], "jac")
        self.assertTrue(steps["board"]["address"].startswith("http"))
        self.assertEqual(steps["forge"], {"configured": False})
        self.assertIsNone(steps["bot"])
        self.assertEqual((steps["team"], steps["general"]), ([], "admin"))
        fleet.set_secret("FORGEJO_URL", "http://forge.test:3000", "jac")
        fleet.set_secret("FORGEJO_TOKEN", "sekret-token-123", "jac")
        fleet.set_secret("FORGEJO_READ_TOKEN", "sekret-read-456", "jac")
        got = self.get()
        self.assertEqual(got.json()["steps"]["forge"], {"configured": True, "url": "http://forge.test:3000", "handle": "jac"})
        for secret in ("sekret-token-123", "sekret-read-456", "password"):
            self.assertNotIn(secret, got.text)

    def test_the_default_bot_and_its_team_are_the_admin_workspaces_bots_in_the_boards_own_words(self):
        keeper = self.register("keeper-box", "keeper")
        self.client.put("/api/fleet/hosts/box", headers=keeper, json={"bots": []}).raise_for_status()      # a box must have reported
        for handle, profile in (("ada", "admin"), ("bea", "developer")):
            r = self.client.put(f"/api/fleet/bots/{handle}", headers=self.jac,
                                json={"host": "box", "run": True, "harness": "claude", "workspace": "admin", "profile": profile})
            self.assertEqual(r.status_code, 200, r.text)
        r = self.client.put("/api/fleet/hosts/box", headers=keeper, json={"bots": [
            {"handle": "ada", "harness": "claude", "status": "waiting"},
            {"handle": "bea", "harness": "claude", "status": "stopped", "health": "claude is not logged in"}]})
        self.assertEqual(r.status_code, 200, r.text)
        steps = self.get().json()["steps"]
        self.assertEqual((steps["bot"]["handle"], steps["bot"]["harness"], steps["bot"]["state"], steps["bot"]["up"]), ("ada", "claude", "waiting", True))
        self.assertNotIn("log", steps["bot"], "the keeper log is named only for a bot that failed")
        self.assertEqual([b["handle"] for b in steps["team"]], ["bea"])
        self.assertTrue(steps["team"][0]["role"], "the role comes from its profile")
        self.assertEqual(json.loads(json.dumps(steps))["team"][0]["state"], steps["team"][0]["state"])


if __name__ == "__main__":
    unittest.main()
