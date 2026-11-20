"""Claim modes (#622): a fleet bot takes new threads open (any that mention nobody) or only when its first message
mentions it. A new bot gets it from its profile, a bot that existed keeps behaving as it did, the board enforces it,
warns when no bot here takes an unmentioned ask, and /fleet, the roster and the Team panel show and set it.
Run with python -m unittest discover -s tests."""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from app import db, fleet, web                  # noqa: E402
from test_progress import Base                    # noqa: E402


class ClaimModes(Base):
    def setUp(self):
        super().setUp()
        self.devs = self.register("dev", "claude-code", "sg")
        self.spec = self.register("spec", "claude-code", "sg")
        self.up("dev", "spec")

    def up(self, *handles, status="waiting"):
        r = self.client.put("/api/fleet/hosts/box", headers=self.keeper, json={"bots": [
            {"handle": h, "harness": "claude", "status": status, "serves": "/pool", "pool": True} for h in handles]})
        self.assertEqual(r.status_code, 200, r.text)

    def add(self, handle, expected=200, **body):
        r = self.client.put(f"/api/fleet/bots/{handle}", headers=self.human,
                            json={"host": "box", "run": True, "harness": "claude", "workspace": "sg", **body})
        self.assertEqual(r.status_code, expected, r.text)
        return r

    def claims(self, handle):
        return db.bot_claims(handle)

    def test_a_new_bot_gets_its_mode_from_its_profile_and_a_specialist_needs_no_channel(self):
        self.add("dev", profile="developer")
        self.add("spec", profile="security-engineer")                  # specialized: no channel needed, it works when mentioned
        self.assertEqual((self.claims("dev"), self.claims("spec")), ("open", "mentioned"))
        r = self.client.put("/api/fleet/bots/spec/claims", headers=self.human, json={"claims": "open"})
        self.assertEqual(r.status_code, 409, r.text)                   # open and specialized: it must be bound first
        self.assertIn("specialized profile", r.text)
        self.assertEqual(self.client.put("/api/fleet/bots/spec/channels", headers=self.human, json={"channels": ["sg"]}).status_code, 200)
        self.assertEqual(self.client.put("/api/fleet/bots/spec/claims", headers=self.human, json={"claims": "open"}).status_code, 200)
        self.assertEqual(self.claims("spec"), "open")
        self.assertEqual(self.client.put("/api/fleet/bots/dev/claims", headers=self.human, json={"claims": "mentioned"}).status_code, 200)
        self.assertEqual(self.claims("dev"), "mentioned")

    def test_only_a_human_sets_it_and_only_to_a_known_mode_of_a_fleet_bot(self):
        self.add("dev", profile="developer")
        self.assertEqual(self.client.put("/api/fleet/bots/dev/claims", headers=self.bot, json={"claims": "open"}).status_code, 403)
        self.assertEqual(self.client.put("/api/fleet/bots/dev/claims", headers=self.human, json={"claims": "sometimes"}).status_code, 422)
        self.assertEqual(self.client.put("/api/fleet/bots/nobody/claims", headers=self.human, json={"claims": "open"}).status_code, 409)
        self.add("dev", expected=422, claims="never")

    def test_a_bot_that_exists_keeps_taking_new_threads_as_it_did(self):
        self.add("dev", profile="artist", channels=[self.art], claims="open")
        c = db.conn()
        c.execute("UPDATE fleet_bots SET claims=NULL"); c.commit()       # as a row from before the column
        self.assertEqual(self.claims("dev"), "open")
        c.execute("ALTER TABLE fleet_bots DROP COLUMN claims"); c.commit()
        fleet.init()                                                       # the migration: every row that exists is open
        self.assertEqual(c.execute("SELECT claims FROM fleet_bots WHERE handle='dev'").fetchone()["claims"], "open")

    def test_a_mentioned_bot_takes_a_new_thread_only_when_it_names_it(self):
        self.add("spec", profile="security-engineer")
        plain = self.post("review this")["id"]
        r = self.claim(plain, self.spec)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("only when its first message mentions it", r.text)
        named = self.post("@spec please review this")["id"]
        self.assertEqual(self.claim(named, self.spec).status_code, 200)
        self.assertEqual(self.worker(named), "spec")
        # an open bot takes the plain one, and is refused one that names another bot
        self.assertEqual(self.claim(plain, self.devs).status_code, 200)
        other = self.post("@spec look at this too")["id"]
        self.assertEqual(self.claim(other, self.devs).status_code, 403)

    def test_a_bot_named_in_a_first_message_holds_the_thread_by_answering_whatever_its_mode(self):
        self.add("spec", profile="security-engineer")
        tid = self.post("@spec is this safe?")["id"]
        self.post("yes, safe", auth=self.spec, reply=tid)
        self.assertEqual(self.worker(tid), "spec")

    def test_a_thread_no_running_bot_takes_unmentioned_is_said_on_the_post(self):
        self.add("dev", profile="developer"); self.add("spec", profile="security-engineer")
        r = self.client.post("/api/messages", headers=self.human, json={"channel": "sg", "body": "do a thing"})
        self.assertNotIn("unclaimed", r.text, "an open, running bot takes it")
        self.client.put("/api/fleet/bots/dev/claims", headers=self.human, json={"claims": "mentioned"})
        r = self.client.post("/api/messages", headers=self.human, json={"channel": "sg", "body": "do another thing"})
        self.assertEqual([w["state"] for w in r.json()["warnings"]], ["unclaimed"], r.text)
        self.assertIn("mention one", r.json()["warnings"][0]["text"])
        r = self.client.post("/api/messages", headers=self.human, json={"channel": "sg", "body": "@dev this one"})
        self.assertNotIn("unclaimed", r.text, "a thread that names someone is not asked about")
        r = self.client.post("/api/messages", headers=self.human, json={"channel": "sg", "body": "a reply", "reply_to": r.json()["message"]["id"]})
        self.assertNotIn("unclaimed", r.text)

    def test_the_roster_and_the_fleet_page_show_it(self):
        self.add("dev", profile="developer"); self.add("spec", profile="security-engineer")
        agents = {a["handle"]: a for a in self.client.get("/api/agents").json()["agents"]}
        self.assertEqual((agents["dev"]["claims"], agents["spec"]["claims"], agents["op"]["claims"]), ("open", "mentioned", None))
        self.assertEqual(self.client.get("/api/agents/spec").json()["agent"]["claims"], "mentioned")
        page = self.client.get("/fleet").text
        self.assertIn('name="claims"', page)
        self.assertIn("only when mentioned", page)
        self.assertIn('class="act claims-edit needs-me" data-handle="spec" data-claims="mentioned"', page)
        self.assertIn("button.claims-edit", page)
        self.assertIn("takes a new thread only when its first message mentions it", self.client.get("/").text, "the hover card says so")

    def test_the_post_to_the_team_panel_sets_no_channels(self):
        self.add("spec", profile="security-engineer", claims="mentioned")
        row = db.conn().execute("SELECT channels, claims FROM fleet_bots WHERE handle='spec'").fetchone()
        self.assertEqual((row["channels"], row["claims"]), (None, "mentioned"))


if __name__ == "__main__":
    unittest.main()
