"""#650: a team template carries how many bots each profile starts with; the workspace keeps it as data, and team()
asks for that many, one for a profile the roster does not list. Run with python -m unittest discover -s tests."""
import unicodedata
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main, team, workspaces


class Roster(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        r = self.client.post("/api/agents", json={"handle": "jac", "kind": "human"})
        self.jac = {"Authorization": "Bearer " + r.json()["token"]}
        self.agent = db.get_agent_by_handle("jac")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.tmp.cleanup()

    def make(self, slug="solo"):
        r = self.client.post("/api/workspaces", headers=self.jac, json={"slug": slug, "name": slug, "purpose": "p"})
        self.assertEqual(r.status_code, 201, r.text)
        return workspaces.find(slug)

    def test_the_parser_reads_counts_by_profile(self):
        self.assertEqual(team.parse_roster("developer=2, Architect=1, x, y=z"), {"developer": 2, "architect": 1})
        self.assertEqual(team.parse_roster("developer=\u00b2, a=999"), {"a": team.MAX_COUNT})
        self.assertEqual(team.parse_roster(""), {})

    def test_a_template_sets_the_agreement_and_the_roster(self):
        ws = self.make()
        self.assertEqual(workspaces.roster(ws), {})
        team.apply(ws, "developers-with-specialists", self.agent)
        self.assertEqual(workspaces.roster(ws), {"developer": 2})
        self.assertTrue(workspaces.kb_current_revision(ws, "team/agreement.md"))   # the default agreement was there first and is kept

    def test_the_server_refuses_invisible_characters_in_the_agreement_and_proposals(self):
        # #679: the rule is the server's, so any writer is covered, not only the page that applies a proposal
        ws = self.make()
        rev = workspaces.kb_current_revision(ws, "team/agreement.md") or 0
        for path, base in (("team/agreement.md", rev), ("team/proposals/7.md", 0)):
            r = self.client.put("/api/workspaces/solo/knowledge/" + path, headers=self.jac,
                                json={"body": "## Roles\n\nreview\u200b first", "base_revision": base, "reason": "t", "sources": []})
            self.assertEqual(r.status_code, 422, r.text)
            self.assertIn("U+200B", r.text)
            self.assertIn("Unicode " + unicodedata.unidata_version, r.text)
        r = self.client.put("/api/workspaces/solo/knowledge/notes/x.md", headers=self.jac,
                            json={"body": "a\u200bb", "base_revision": 0, "reason": "t", "sources": []})
        self.assertIn(r.status_code, (200, 201), r.text)      # other documents are not touched by the rule

    def test_team_wants_the_rosters_count_and_one_otherwise(self):
        ws = self.make()
        workspaces.kb_put(ws, "team/agreement.md", "## Roles\n\n| Role | Profile |\n|---|---|\n| Developer | developer |\n| Architect | architect |\n",
                          workspaces.kb_current_revision(ws, "team/agreement.md") or 0, "t", [], self.agent)
        workspaces.set_roster(ws, {"developer": 2})
        roles = {r["profile"]: r for r in workspaces.team(ws)["roles"]}
        self.assertEqual(roles["developer"]["want"], 2)
        self.assertEqual(roles["architect"]["want"], 1)
        self.assertEqual(roles["developer"]["missing"], 2)      # no bot yet: two to create

    def test_the_first_human_gets_a_general_workspace_with_the_preset_team_once(self):
        ws = workspaces.find("general")
        self.assertEqual(workspaces.roster(ws), {"developer": 2})
        body = workspaces.kb_get(ws, "team/agreement.md")["body"]
        self.assertIn("pool of developers", team.template("developers-with-specialists")["description"])
        self.assertIn("Developers form one pool", body)
        self.assertEqual({r["profile"] for r in workspaces.team(ws)["roles"]} >= {"developer", "architect"}, True)
        self.assertEqual(db.get_board("general")["workspace_id"], ws["id"])      # it adopted the board's own #general
        self.client.post("/api/agents", json={"handle": "second", "kind": "human"})
        self.assertEqual(sorted(w["slug"] for w in self.client.get("/api/workspaces", headers=self.jac).json()["workspaces"]),
                         ["admin", "general"])

    def test_a_board_that_has_been_used_gets_no_general_workspace(self):
        """gina: "first human" is not "new board": a deployed board with a bot or a message already is left alone."""
        for setup in ("bot", "message"):
            with self.subTest(setup):
                self.tearDown()
                self.tmp = tempfile.TemporaryDirectory()
                db.DB_PATH = os.path.join(self.tmp.name, "board.db")
                db._local = threading.local()
                self.client = TestClient(main.app)
                self.client.__enter__()
                if setup == "bot":
                    self.client.post("/api/agents", json={"handle": "old", "kind": "claude-code"})
                else:      # a message from the board's own agent, the one kind that does not count as a bot
                    from app import progress
                    db.post_message("general", progress.board_agent()["id"], "hello")
                self.client.post("/api/agents", json={"handle": "jac", "kind": "human"})
                self.assertRaises(KeyError, workspaces.find, "general")
                self.assertTrue(db.conn().execute("SELECT 1 FROM registry_meta WHERE key='first_run_workspace'").fetchone())


if __name__ == "__main__":
    unittest.main()
