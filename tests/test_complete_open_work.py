"""#931: a thread cannot be completed while a pull request labelled `thread:<id>` is open in its workspace's repository.
The forge is asked, nothing is stored. A forge that cannot answer does not stop completion: it says so in `warnings`."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, fleet, forge, main, workspaces


def item(n, label, pull=True, state="open"):
    return {"number": n, "state": state, "labels": [{"name": label}], **({"pull_request": {}} if pull else {})}


class CompleteOpenWork(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        r = self.client.post("/api/agents", json={"handle": "jac", "kind": "human"})
        self.jac = {"Authorization": "Bearer " + r.json()["token"]}
        self.agent = db.get_agent_by_handle("jac")
        self.root = self.client.post("/api/messages", headers=self.jac, json={"channel": "general", "body": "work"}).json()["message"]["id"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.tmp.cleanup()

    def forge_set(self):
        workspaces.set_repository("general", "fleet/general", self.agent)
        fleet.set_secret("FORGEJO_URL", "http://forge.invalid:3000", "jac")
        fleet.set_secret("FORGEJO_TOKEN", "t", "jac")

    def complete(self):
        return self.client.post(f"/api/threads/{self.root}/complete", headers=self.jac, json={"through": self.root})

    def answer(self, data, status=200):
        return mock.patch.object(forge, "_call", return_value=(status, data))

    def test_an_open_pull_request_with_the_label_refuses_and_names_it(self):
        self.forge_set()
        with self.answer([item(858, f"thread:{self.root}"), item(9, f"thread:{self.root}", pull=False)]) as ask:
            r = self.complete()
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("PR #858 is open: merge or close it, or hand the thread on", r.json()["error"])
        self.assertNotIn("#9 ", r.json()["error"], "an issue with the thread's label is meant to stay open: only pull requests block")
        path = ask.call_args.args[1]
        self.assertIn("/api/v1/repos/fleet/general/issues?", path)
        self.assertIn(f"labels=thread%3A{self.root}", path)
        self.assertIn("state=open", path)
        self.assertIn("type=pulls", path)
        self.assertIsNone(self.client.get(f"/api/threads/{self.root}", headers=self.jac).json()["messages"][0]["completed_through"])

    def test_nothing_open_completes_without_a_warning(self):
        self.forge_set()
        with self.answer([]):
            r = self.complete()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"thread_id": self.root, "completed_through": self.root})

    def test_an_item_with_another_threads_label_or_closed_does_not_block(self):
        self.forge_set()
        with self.answer([item(1, f"thread:{self.root}9"), item(2, f"thread:{self.root}", state="closed")]):
            self.assertEqual(self.complete().status_code, 200)

    def test_a_forge_that_is_down_completes_with_a_warning(self):
        self.forge_set()
        with mock.patch.object(forge, "_call", side_effect=forge.ForgeError("the forge did not answer: refused")):
            r = self.complete()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("could not be asked", r.json()["warnings"][0])

    def test_a_forge_that_answers_badly_completes_with_a_warning(self):
        self.forge_set()
        with self.answer(None, status=500):
            r = self.complete()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["warnings"])

    def test_no_repository_completes_with_a_warning(self):
        with mock.patch.object(forge, "_call", side_effect=AssertionError("no call")):
            r = self.complete()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("no repository", r.json()["warnings"][0])

    def test_no_forge_address_completes_with_a_warning(self):
        workspaces.set_repository("general", "fleet/general", self.agent)
        with mock.patch.object(forge, "_call", side_effect=AssertionError("no call")):
            r = self.complete()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("no forge address", r.json()["warnings"][0])

    def test_the_client_builds_label_and_path_itself(self):
        with self.answer([]) as ask:
            forge.open_for_thread("fleet/general", 42)
        self.assertEqual(ask.call_args.args[:1], ("GET",))
        self.assertTrue(ask.call_args.kwargs["read"])
        with self.assertRaises(forge.ForgeError):
            forge.open_for_thread("fleet/../x", 1)
        with self.assertRaises(ValueError):
            forge.open_for_thread("fleet/general", "1; drop")


if __name__ == "__main__":
    unittest.main()
