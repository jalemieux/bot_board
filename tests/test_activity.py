"""#738: the Activity tab's data. Board counts always; the forge's counts only when the workspace has a repository and the
forge answers; every count is the length of its events. Run with python -m unittest discover -s tests."""
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import activity, db, forge, main, workspaces

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
FORGE = {
    "pulls": [{"number": 7, "title": "A fix", "html_url": "http://f/pulls/7", "created_at": "2026-11-23T09:00:00Z", "merged": True,
               "merged_at": "2026-11-23T10:00:00Z", "labels": [{"name": "bot:ken"}, {"name": "thread:1"}], "body": ""},
              {"number": 5, "title": "Old", "html_url": "http://f/pulls/5", "created_at": "2026-11-01T09:00:00Z", "merged": True,
               "merged_at": "2026-11-02T10:00:00Z", "labels": [], "body": ""}],
    "issues": [{"number": 8, "title": "A bug", "html_url": "http://f/issues/8", "created_at": "2026-11-23T08:00:00Z", "state": "closed",
                "closed_at": "2026-11-23T11:00:00Z", "labels": [], "body": "bot ray, board thread 3\n\nx"},
               {"number": 7, "title": "A fix", "pull_request": {}, "created_at": "2026-11-23T09:00:00Z", "state": "open", "labels": []}],
    "commits": [{"sha": "abcdef123456", "html_url": "http://f/c/abc", "commit": {"message": "Fix it\n\nlong", "committer": {"date": "2026-11-23T09:30:00Z"}}}],
    "reviews": {7: [{"submitted_at": "2026-11-23T09:50:00Z", "state": "COMMENT", "body": "bot gina, board thread 1\n\nGood to merge", "html_url": "http://f/r"}]},
}


class Activity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        r = self.client.post("/api/agents", json={"handle": "jac", "kind": "human"})
        self.jac = {"Authorization": "Bearer " + r.json()["token"]}
        self.agent = db.get_agent_by_handle("jac")
        self.ws = workspaces.find("general")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.tmp.cleanup()

    def say(self, body):
        self.client.post("/api/messages", headers=self.jac, json={"channel": "general", "body": body}).raise_for_status()

    def test_a_workspace_with_no_repository_has_the_boards_counts_only(self):
        self.say("hello")
        a = activity.build(self.ws, now=datetime.now(timezone.utc))
        self.assertEqual(a["forge"], "none")
        self.assertEqual(a["counts"], {"thread": 1, "message": 1, "bots": 0})
        self.assertEqual([e["kind"] for e in a["events"]], ["thread"])

    def test_the_forge_adds_its_counts_and_each_count_is_its_events(self):
        workspaces.set_repository("general", "jac/x", self.agent)
        self.ws = workspaces.find("general")
        with mock.patch.object(forge, "recent_activity", return_value=FORGE):
            a = activity.build(self.ws, now=NOW)
        self.assertEqual((a["forge"], a["capped"]), ("ok", False))
        self.assertEqual({k: a["counts"][k] for k in ("merged", "opened", "closed", "review", "commit")},
                         {"merged": 1, "opened": 2, "closed": 1, "review": 1, "commit": 1})       # PR 5 is older than the window
        for kind in ("merged", "opened", "closed", "review", "commit"):
            self.assertEqual(a["counts"][kind], sum(1 for e in a["events"] if e["kind"] == kind), kind)
        self.assertEqual([e["by"] for e in a["events"] if e["kind"] == "merged"], ["ken"])      # the bot label
        self.assertEqual([e["by"] for e in a["events"] if e["text"].startswith("Issue #8 closed")], ["ray"])    # the signed line
        self.assertEqual([e["by"] for e in a["events"] if e["kind"] == "commit"], [""])         # unknown reads as unknown
        self.assertEqual([e["at"] for e in a["events"]], sorted((e["at"] for e in a["events"]), reverse=True))

    def test_a_forge_that_does_not_answer_leaves_the_boards_part_and_says_why(self):
        workspaces.set_repository("general", "jac/x", self.agent)
        self.ws = workspaces.find("general")
        with mock.patch.object(forge, "recent_activity", side_effect=forge.ForgeError("the forge did not answer: refused")):
            a = activity.build(self.ws, now=datetime.now(timezone.utc))
        self.assertEqual((a["forge"], a["reason"]), ("down", "the forge did not answer: refused"))
        self.assertNotIn("merged", a["counts"])         # unknown is not zero

    def test_the_route_answers_and_limits_the_window(self):
        r = self.client.get("/api/workspaces/general/activity", headers=self.jac)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["activity"]["hours"], 24)
        self.assertEqual(self.client.get("/api/workspaces/general/activity?hours=999", headers=self.jac).status_code, 422)


if __name__ == "__main__":
    unittest.main()
