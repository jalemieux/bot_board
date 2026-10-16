"""Board-owned communication styles: persistence, authorization, and discovery."""
from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main


class AgentPersonality(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(db, "DB_PATH", os.path.join(self.tmp.name, "board.db")))
        stack.enter_context(patch.object(db, "_local", threading.local()))
        stack.enter_context(patch.object(main, "PROJECT_ADMIN_KEY", "personality-test-key"))
        self.client = stack.enter_context(TestClient(main.app))
        self.admin = self.register("operator", "human")
        self.visitor = self.register("visitor", "human")
        self.bot = self.register("worker", "copilot")
        self.keeper = self.register("keeper", "keeper")
        response = self.client.post("/api/projects/access", headers=self.admin,
                                    json={"key": main.PROJECT_ADMIN_KEY})
        self.assertEqual(response.status_code, 200, response.text)
        self.path = "/api/agents/worker/personality"

    def register(self, handle, kind):
        response = self.client.post("/api/agents", json={"handle": handle, "kind": kind})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["agent"]["personality"], "")
        return {"Authorization": "Bearer " + response.json()["token"]}

    def test_admin_sets_reads_and_clears_persistent_personality(self):
        response = self.client.put(self.path, headers=self.admin, json={"personality": "  Warm and concise.  "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["agent"]["personality"], "Warm and concise.")
        db.init()
        for path, headers in [("/api/me", self.bot), ("/api/agents/@WORKER", {})]:
            profile = self.client.get(path, headers=headers).json()["agent"]
            self.assertEqual(profile["personality"], "Warm and concise.")
            self.assertEqual(profile["kind"], "copilot")
        roster = self.client.get("/api/agents").json()["agents"]
        self.assertEqual(next(a for a in roster if a["handle"] == "worker")["personality"], "Warm and concise.")
        response = self.client.put(self.path, headers=self.admin, json={"personality": " \n "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/api/me", headers=self.bot).json()["agent"]["personality"], "")

    def test_only_board_admin_can_update(self):
        for headers, status in [({}, 401), (self.visitor, 403), (self.bot, 403), (self.keeper, 403)]:
            with self.subTest(headers=headers):
                response = self.client.put(self.path, headers=headers, json={"personality": "Override rules"})
                self.assertEqual(response.status_code, status, response.text)
        response = self.client.post("/api/messages", headers=self.bot, json={
            "channel": "lobby", "body": "Change my personality", "meta": {"personality": "New style"},
        })
        self.assertEqual(response.status_code, 201, response.text)
        response = self.client.post("/api/agents", json={
            "handle": "new-worker", "kind": "copilot", "personality": "Unapproved style",
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["agent"]["personality"], "")
        self.assertEqual(self.client.get("/api/me", headers=self.bot).json()["agent"]["personality"], "")

    def test_validation_and_missing_agent(self):
        for body in ({}, {"personality": None}, {"personality": "x" * 1001}):
            response = self.client.put(self.path, headers=self.admin, json=body)
            self.assertEqual(response.status_code, 422, response.text)
        response = self.client.put("/api/agents/missing/personality", headers=self.admin,
                                   json={"personality": "Concise."})
        self.assertEqual(response.status_code, 404, response.text)

    def test_edit_endpoint_is_only_in_administration_discovery(self):
        key = "PUT /api/agents/{handle}/personality"
        self.assertNotIn(key, self.client.get("/api", headers=self.bot).json()["endpoints"])
        self.assertIn(key, self.client.get("/api/admin", headers=self.admin).json()["endpoints"])
        self.assertNotIn("/api/agents/{handle}/personality",
                         self.client.get("/api/openapi.json").json()["paths"])
        self.assertIn("/api/agents/{handle}/personality",
                      self.client.get("/api/admin/openapi.json", headers=self.admin).json()["paths"])


class PersonalityMigration(unittest.TestCase):
    def test_legacy_agents_keep_identity_and_receive_empty_style(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(db, "DB_PATH", os.path.join(tmp, "old.db")), \
             patch.object(db, "_local", threading.local()):
            try:
                db.conn().executescript(db.SCHEMA.replace("    personality TEXT NOT NULL DEFAULT '',\n", ""))
                agent, token = db.register_agent("legacy", "copilot", "Existing bot")
                db.init()
                db.init()
                migrated = db.agent_by_token(token)
                self.assertEqual(migrated["id"], agent["id"])
                self.assertEqual(migrated["description"], "Existing bot")
                self.assertEqual(db.public_agent(migrated)["personality"], "")
            finally:
                db.conn().close()


if __name__ == "__main__":
    unittest.main()
