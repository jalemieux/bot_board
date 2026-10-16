"""Atomic pickup and delivery: one worker, helpers only by mention."""
import concurrent.futures
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main


class ThreadWorkers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = patch.object(db, "DB_PATH", os.path.join(self.tmp.name, "board.db"))
        self.local = patch.object(db, "_local", threading.local())
        self.path.start()
        self.local.start()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register("operator", "human")
        self.worker = self.register("worker", "copilot")
        self.helper = self.register("helper", "copilot")
        self.other = self.register("other", "copilot")
        self.root = self.post(self.human, "Please build the script.")
        self.endpoint = f'/api/threads/{self.root["id"]}/worker'

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.local.stop()
        self.path.stop()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post("/api/agents", json={"handle": handle, "kind": kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": "Bearer " + r.json()["token"]}

    def post(self, auth, body, reply=None, channel="work"):
        r = self.client.post("/api/messages", headers=auth, json={
            "channel": channel, "body": body, "reply_to": reply,
        })
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["message"]

    def inbox(self, auth, since=0):
        r = self.client.get("/api/inbox", headers=auth, params={"since": since})
        self.assertEqual(r.status_code, 200, r.text)
        return [m["id"] for m in r.json()["messages"]]

    def claim(self, auth):
        return self.client.post(self.endpoint, headers=auth)

    def test_simultaneous_api_claims_have_one_winner_and_no_claim_messages(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda auth: self.claim(auth).status_code,
                                     [self.worker, self.helper]))
        self.assertEqual(sorted(statuses), [200, 409])
        state = self.client.get(self.endpoint).json()
        winner = self.worker if state["worker"] == "worker" else self.helper
        self.assertEqual(self.claim(winner).json(), state)
        thread = self.client.get(f'/api/threads/{self.root["id"]}').json()
        self.assertEqual(thread["worker"], state["worker"])
        self.assertEqual(len(thread["messages"]), 1)
        self.assertEqual(thread["messages"][0]["worker"], state["worker"])

    def test_independent_processes_cannot_both_claim(self):
        script = """
import sys
from app import db
db.DB_PATH = sys.argv[1]
agent = db.get_agent_by_handle(sys.argv[3])
try:
    db.claim_thread(int(sys.argv[2]), agent)
    print("won")
except ValueError:
    print("lost")
"""
        processes = [subprocess.Popen(
            [sys.executable, "-c", script, db.DB_PATH, str(self.root["id"]), handle],
            cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        ) for handle in ("worker", "helper")]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(stdout.strip())
        self.assertEqual(sorted(results), ["lost", "won"])

    def test_only_worker_follows_replies_and_mentions_do_not_subscribe_helpers(self):
        self.assertEqual(self.claim(self.worker).status_code, 200)
        rid = self.root["id"]
        invite = self.post(self.worker, "@helper, where is the mapping?", rid)
        self.assertEqual(self.inbox(self.helper), [invite["id"]])
        answer = self.post(self.helper, "Here is the mapping.", rid)
        followup = self.post(self.human, "Also support resume.", rid)
        tagged = self.post(self.human, "@helper, does it include Notes?", rid)
        final = self.post(self.helper, "Yes, it does.", tagged["id"])
        self.assertEqual(self.inbox(self.worker, rid),
                         [answer["id"], followup["id"], tagged["id"], final["id"]])
        self.assertEqual(self.inbox(self.helper), [invite["id"], tagged["id"]])
        self.assertEqual(self.inbox(self.other), [])
        self.assertEqual(self.inbox(self.human), [])
        self.assertEqual(self.client.get(self.endpoint).json()["worker"], "worker")

    def test_posting_and_backing_off_never_subscribe(self):
        self.post(self.helper, "Backing off.", self.root["id"])
        self.post(self.human, "More details.", self.root["id"])
        self.assertEqual(self.inbox(self.helper), [])

    def test_owner_survives_database_reinitialization_and_paging(self):
        self.assertEqual(self.claim(self.worker).status_code, 200)
        state = self.client.get(self.endpoint).json()
        db.init()
        self.assertEqual(self.client.get(self.endpoint).json(), state)
        self.post(self.human, "Follow-up", self.root["id"])
        page = self.client.get("/api/inbox", headers=self.worker, params={"limit": 1, "view": "compact"}).json()
        self.assertTrue(page["has_more"])
        self.assertEqual(page["messages"][0]["worker"], "worker")
        next_page = self.client.get("/api/inbox", headers=self.worker,
                                    params={"since": page["next_since"]}).json()
        self.assertEqual(len(next_page["messages"]), 1)

    def test_release_permissions_and_reassignment(self):
        self.assertEqual(self.claim(self.worker).status_code, 200)
        self.assertEqual(self.client.delete(self.endpoint, headers=self.helper).status_code, 403)
        self.assertEqual(self.client.delete(self.endpoint, headers=self.human).status_code, 200)
        self.assertIsNone(self.client.get(self.endpoint).json()["worker"])
        thread = self.client.get(f'/api/threads/{self.root["id"]}').json()["messages"]
        self.assertEqual(thread[-1]["meta"]["kind"], "thread_released")
        self.assertEqual(self.claim(self.helper).status_code, 200)
        self.assertEqual(self.claim(self.worker).status_code, 409)
        followup = self.post(self.human, "Continue here.", self.root["id"])
        self.assertNotIn(followup["id"], self.inbox(self.worker))
        self.assertIn(followup["id"], self.inbox(self.helper))
        self.assertEqual(self.client.delete(self.endpoint, headers=self.helper).status_code, 200)

    def test_claim_validation_and_targeted_roots(self):
        self.assertEqual(self.claim(self.human).status_code, 403)
        self.assertEqual(self.client.post("/api/threads/999999/worker", headers=self.worker).status_code, 404)
        reply = self.post(self.human, "Details.", self.root["id"])
        self.assertEqual(self.client.post(f'/api/threads/{reply["id"]}/worker',
                                         headers=self.worker).status_code, 409)
        targeted = self.post(self.human, "@helper please handle this.")
        path = f'/api/threads/{targeted["id"]}/worker'
        self.assertEqual(self.client.post(path, headers=self.worker).status_code, 403)
        self.assertEqual(self.client.post(path, headers=self.helper).status_code, 200)

    def test_conversation_membership_is_not_a_reply_subscription(self):
        conversation = self.client.post("/api/conversations", headers=self.worker,
                                       json={"participants": ["helper"]}).json()["conversation"]
        root = self.post(self.worker, "@helper, inspect this.", channel=conversation["slug"])
        path = f'/api/threads/{root["id"]}/worker'
        self.assertEqual(self.client.post(path, headers=self.other).status_code, 403)
        self.assertEqual(self.client.post(path, headers=self.helper).status_code, 200)
        self.post(self.helper, "Done.", root["id"], channel=conversation["slug"])
        self.assertEqual(self.inbox(self.worker), [])


if __name__ == "__main__":
    unittest.main()
