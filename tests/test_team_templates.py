"""Team agreement templates and seeding (#622): the base template and the team templates keep the same sections,
the board serves them, a new workspace starts with the default agreement and an old one gets it once.
Run with python -m unittest discover -s tests."""
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from app import db, registry, team, workspaces     # noqa: E402
from test_workspaces import Base                    # noqa: E402


def sections(text):
    return re.findall(r"^## (.+)$", text, re.M)


def without_asks(text):
    out, skipping = [], False
    for line in text.split("\n"):
        if line.startswith("> Ask:"):
            skipping = True
        elif skipping and not line.startswith(">"):
            skipping = False
        if not skipping:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


class Files(unittest.TestCase):
    def test_every_agreement_keeps_the_base_templates_sections_in_order(self):
        self.assertEqual(sections(team.base()), list(team.SECTIONS))
        for t in team.templates():
            self.assertEqual(sections(team.template(t["name"])["body"]), list(team.SECTIONS), t["name"])

    def test_each_base_section_ends_with_the_question_to_ask(self):
        parts = re.split(r"^## ", team.base(), flags=re.M)[1:]
        self.assertEqual(len(parts), len(team.SECTIONS))
        for part in parts:
            self.assertIn("> Ask:", part, part.split("\n")[0])

    def test_the_default_is_the_base_template_without_its_questions(self):
        self.assertEqual(team.template("default")["body"].strip(), without_asks(team.base()))

    def test_the_team_templates_have_a_name_title_and_description(self):
        names = [t["name"] for t in team.templates()]
        self.assertEqual(names, ["default", "developers-with-specialists"])
        for t in team.templates():
            self.assertTrue(t["title"] and t["description"], t["name"])
        roles = team.template("developers-with-specialists")["body"].split("## Roles")[1]
        self.assertEqual(re.findall(r"^\| (?!Role|---)([^|]+) \|", roles, re.M),
                         ["Developer (pool)", "UI designer", "Architect", "Security engineer"])

    def test_an_unknown_or_unsafe_name_is_not_a_template(self):
        for name in ("nope", "../agreement-template", "a/b", ""):
            with self.assertRaises(KeyError):
                team.template(name)

    def test_the_team_setup_skill_and_the_admin_starter_ship(self):
        skill = (ROOT / "docs" / "skills" / "team-setup" / "SKILL.md").read_text()
        front, body = registry.parse_front(skill)
        self.assertEqual(front["name"], "team-setup")
        for needle in ("GET /api/team/template", "GET /api/team/templates", "Never edit a profile or skill that already exists",
                       "Only a human's clear yes", "through the base skill `forgejo-lifecycle`"):
            self.assertIn(needle, body)
        admin = [t for t in registry.templates() if t["name"] == "admin"]
        self.assertEqual([s["name"] if isinstance(s, dict) else s for s in admin[0]["skills"]], ["team-setup"])
        self.assertNotIn("Write nothing before a clear yes", (ROOT / "ADMIN.md").read_text().split("## Where attachments live")[0]
                         .split("## Team setup for a workspace")[1][:800], "the steps live in the skill")


class Image(unittest.TestCase):
    def test_the_docker_image_carries_the_templates(self):
        self.assertIn("COPY docs/team ./docs/team", (ROOT / "Dockerfile").read_text())


class Board(Base):
    def setUp(self):
        super().setUp()
        self.human = self.register("jac", "human")
        self.bot = self.register("ada", "claude-code")

    def agreement(self, slug, auth=None):
        return self.call("GET", f"/api/workspaces/{slug}/knowledge/team/agreement.md", auth=auth or self.human)["document"]

    def test_the_board_serves_the_templates(self):
        self.assertEqual(self.call("GET", "/api/team/template", auth=self.bot)["template"], team.base())
        self.assertEqual([t["name"] for t in self.call("GET", "/api/team/templates", auth=self.bot)["templates"]],
                         ["default", "developers-with-specialists"])
        one = self.call("GET", "/api/team/templates/developers-with-specialists", auth=self.bot)["template"]
        self.assertIn("## Roles", one["body"])
        self.call("GET", "/api/team/templates/nope", auth=self.bot, expected=404)

    def test_a_new_workspace_starts_with_the_default_agreement(self):
        self.call("POST", "/api/workspaces", {"slug": "crew", "name": "Crew", "purpose": "x"}, auth=self.human, expected=201)
        doc = self.agreement("crew")
        self.assertEqual((doc["revision"], doc["author"]), (1, "jac"))
        self.assertEqual(doc["body"].strip(), team.template("default")["body"].strip())
        self.assertNotIn("> Ask:", doc["body"])

    def test_a_build_without_the_templates_still_makes_the_workspace(self):
        from unittest import mock
        with mock.patch.object(team, "TEMPLATES_DIR", "/nonexistent"):
            self.call("POST", "/api/workspaces", {"slug": "bare", "name": "Bare", "purpose": "x"}, auth=self.human, expected=201)
        self.call("GET", "/api/workspaces/bare/knowledge/team/agreement.md", auth=self.human, expected=404)

    def test_the_admin_workspace_is_not_a_team(self):
        self.call("POST", "/api/workspaces", {"slug": "crew", "name": "Crew", "purpose": "x"}, auth=self.human, expected=201)
        admin = workspaces.find(workspaces.ADMIN)
        self.assertEqual(workspaces.kb_current_revision(admin, team.AGREEMENT), 0)

    def test_the_backfill_gives_old_workspaces_an_agreement_once(self):
        for slug in ("old", "bot_board", "kept"):     # a workspace named for the product gets the default like any other
            self.call("POST", "/api/workspaces", {"slug": slug, "name": slug, "purpose": "x"}, auth=self.human, expected=201)
        c = db.conn()
        for slug in ("old", "bot_board"):                 # as a workspace made before this change: no agreement at all
            c.execute("DELETE FROM knowledge_docs WHERE path=? AND workspace_id=(SELECT id FROM workspaces WHERE slug=?)", (team.AGREEMENT, slug))
        c.execute("DELETE FROM registry_meta WHERE key='team_agreements_backfilled'")
        c.commit()
        kept = self.agreement("kept")["body"]
        self.assertEqual(team.backfill(), 2, "the two without one; the third has its own")
        self.assertEqual(self.agreement("old")["body"].strip(), team.template("default")["body"].strip())
        named = self.agreement("bot_board")
        self.assertEqual(named["body"].strip(), team.template("default")["body"].strip())
        self.assertNotIn("jade-dc77", named["body"], "no board's people are shipped in the product")
        self.assertEqual(self.agreement("kept")["body"], kept)
        # a human deletes one: the backfill does not bring it back
        self.call("DELETE", "/api/workspaces/old/knowledge/team/agreement.md", {"base_revision": 1, "reason": "no"}, auth=self.human)
        self.assertEqual(team.backfill(), 0)
        self.call("GET", "/api/workspaces/old/knowledge/team/agreement.md", auth=self.human, expected=404)


if __name__ == "__main__":
    unittest.main()
