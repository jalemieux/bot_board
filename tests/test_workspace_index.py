"""#810: a new workspace starts with an INDEX.md that points at its agreement and its repository, and the guide of its bots names
the repository, or says there is none. Run with python -m unittest discover -s tests."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, fleet, registry, workspaces


class WorkspaceIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        db.init()
        fleet.init()
        workspaces.init()
        registry.init()
        self.jac, _ = db.register_agent("jac", "human", "", None, False)

    def make(self, slug="proj", purpose="Where the game is made.", **more):
        workspaces.create({"slug": slug, "name": slug.title(), "purpose": purpose, "team_template": "default", **more}, self.jac)
        return workspaces.find(slug)

    def bot(self, ws, handle="ada"):
        return db.register_agent(handle, "claude-code", "", ws["id"], False)[0]

    def test_a_new_workspace_gets_an_index_that_points_and_does_not_copy(self):
        ws = self.make()
        body = workspaces.kb_get(ws, "INDEX.md")["body"]
        self.assertIn("# Proj", body)
        self.assertIn("Where the game is made.", body)
        self.assertIn("`team/agreement.md`", body)
        self.assertIn("GET /api/workspaces/proj", body)
        self.assertIn("code/repo.md", body)
        self.assertNotIn("fleet/proj", body, "the repository is on the workspace, not copied into the text")

    def test_only_the_first_run_path_seeds_it_so_other_workspaces_are_as_they_were(self):
        workspaces.create({"slug": "plain", "name": "Plain", "purpose": "x"}, self.jac)
        self.assertEqual([d["path"] for d in workspaces.kb_list(workspaces.find("plain"))["documents"]], ["team/agreement.md"])

    def test_an_index_that_is_there_is_never_replaced(self):
        ws = self.make()
        workspaces.kb_put(ws, "INDEX.md", "# Mine\n", workspaces.kb_current_revision(ws, "INDEX.md"), "a human wrote it", [], self.jac)
        self.assertFalse(workspaces.seed_index(ws, self.jac))
        self.assertEqual(workspaces.kb_get(ws, "INDEX.md")["body"], "# Mine\n")

    def test_the_admin_workspace_keeps_its_own_index(self):
        workspaces.ensure_admin(self.jac)
        body = workspaces.kb_get(workspaces.find(workspaces.ADMIN), "INDEX.md")["body"]
        self.assertTrue(body.startswith("# The admin workspace"))

    def test_the_guide_says_there_is_no_repository_and_then_names_it(self):
        ws = self.make()
        ada = self.bot(ws)
        text = registry.identity_text(ada, "http://board.example:8080")
        self.assertIn("It has no repository linked yet", text)
        self.assertIn("PUT http://board.example:8080/api/workspaces/proj/repository", text)
        workspaces.set_repository("proj", "fleet/proj", self.jac)
        text = registry.identity_text(ada, "http://board.example:8080")
        self.assertIn("Its repository is `fleet/proj`", text)
        self.assertIn("`repository` field of `GET http://board.example:8080/api/workspaces/proj`", text)
        self.assertNotIn("no repository linked", text)

    def test_the_guide_gives_the_address_when_the_forge_has_one(self):
        ws = self.make()
        ada = self.bot(ws)
        fleet.set_secret("FORGEJO_URL", "http://forge.example:3000", "jac")
        workspaces.set_repository("proj", "fleet/proj", self.jac)
        self.assertIn("Its repository is `fleet/proj`, at http://forge.example:3000/fleet/proj.", registry.identity_text(ada, "http://b:8080"))


if __name__ == "__main__":
    unittest.main()
