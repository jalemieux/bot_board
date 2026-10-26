"""Who may claim, and the board keeping work moving (board thread 8378).
Run with python -m unittest discover -s tests."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["BOT_BOARD_PROGRESS"] = "0"      # the sweep is called by hand here
from fastapi.testclient import TestClient
from app import db, main, progress

LONG_AGO = "2026-01-01T00:00:00+00:00"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register("op", "human")
        self.keeper = self.register("keeper-box", "keeper")
        self.client.post("/api/workspaces", headers=self.human, json={"slug": "sg", "name": "SG", "purpose": "x"})
        r = self.client.post("/api/workspaces/sg/channels", headers=self.human,
                             json={"slug": "art", "name": "Art", "description": "paintings"})
        self.assertEqual(r.status_code, 201, r.text)
        self.art = r.json()["channel"]["slug"]
        self.bot = self.register("edie", "claude-code", "sg")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind, workspace=None, **extra):
        r = self.client.post("/api/agents", json={"handle": handle, "kind": kind, "workspace": workspace, **extra})
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": "Bearer " + r.json()["token"]}

    def post(self, body, channel="sg", auth=None, reply=None):
        r = self.client.post("/api/messages", headers=auth or self.human,
                             json={"channel": channel, "body": body, "reply_to": reply})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["message"]

    def claim(self, tid, auth=None):
        return self.client.post(f"/api/threads/{tid}/worker", headers=auth or self.bot)

    def worker(self, tid):
        return self.client.get(f"/api/threads/{tid}/worker").json()["worker"]

    def report(self, handle, status):
        r = self.client.put("/api/fleet/hosts/box", headers=self.keeper, json={"bots": [
            {"handle": handle, "harness": "claude", "status": status, "serves": "/pool", "pool": True}]})
        self.assertEqual(r.status_code, 200, r.text)

    def add(self, handle, expected=200, **body):
        r = self.client.put(f"/api/fleet/bots/{handle}", headers=self.human,
                            json={"host": "box", "run": True, "harness": "claude", "workspace": "sg", **body})
        self.assertEqual(r.status_code, expected, r.text)
        return r

    def messages(self, tid):
        return self.client.get(f"/api/threads/{tid}").json()["messages"]

    def silent(self, handle, at=LONG_AGO):
        db.conn().execute("UPDATE agents SET last_seen_at=? WHERE handle=?", (at, handle))
        db.conn().commit()


class WhoMayClaim(Base):
    def test_a_bot_bound_to_channels_claims_only_there(self):
        self.report("edie", "waiting")
        self.add("edie", channels=[self.art])
        bot = next(b for b in self.client.get("/api/fleet").json()["hosts"][0]["bots"] if b["handle"] == "edie")
        self.assertEqual(bot["channels"], [self.art])
        general = self.post("general work")["id"]
        painting = self.post("a painting", channel=self.art)["id"]
        r = self.claim(general)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("bound to " + self.art, r.json()["error"] if "error" in r.json() else r.text)
        self.assertEqual(self.claim(painting).status_code, 200)
        # mentioned elsewhere, it still takes part: a reply needs no claim
        self.post("@edie what do you think?", reply=general)
        # unbound again: anywhere
        r = self.client.put("/api/fleet/bots/edie/channels", headers=self.human, json={"channels": []})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.claim(general).status_code, 200)
        page = self.client.get("/fleet").text
        self.assertIn('id="channels-data"', page)
        self.assertIn('name="channels" multiple', page)

    def test_channels_must_belong_to_the_workspace(self):
        self.report("edie", "waiting")
        r = self.add("edie", expected=409, channels=["elsewhere"])
        self.assertIn("not a channel of sg", r.text)
        r = self.client.put("/api/fleet/bots/edie/channels", headers=self.human, json={"channels": ["nope"]})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.client.put("/api/fleet/bots/edie/channels", headers=self.bot,
                                         json={"channels": []}).status_code, 403)

    def test_a_specialized_profile_needs_a_channel(self):
        self.report("lena", "waiting")
        self.assertTrue(self.client.get("/api/profiles/artist").json()["profile"]["specialized"])
        r = self.add("lena", expected=409, profile="artist")
        self.assertIn("specialized profile", r.text)
        self.add("lena", profile="artist", channels=[self.art])
        r = self.client.put("/api/fleet/bots/lena/channels", headers=self.human, json={"channels": []})
        self.assertEqual(r.status_code, 409, r.text)      # cannot unbind a specialized bot
        self.assertIn("Specialized", self.client.get("/fleet/profiles/artist").text)
        # a new profile can be created specialized, and a developer is not
        r = self.client.post("/api/profiles", headers=self.human,
                             json={"name": "sound", "role": "sounds", "specialized": True})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["profile"]["specialized"])
        self.assertFalse(self.client.get("/api/profiles/developer").json()["profile"]["specialized"])
        self.assertIn("**Specialized.**", self.client.get("/api/profiles/sound").json()["profile"]["text"])

    def test_an_interactive_session_never_claims(self):
        me = self.register("wren", "claude-code", "sg", interactive=True)
        tid = self.post("work")["id"]
        r = self.claim(tid, me)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("interactive session", r.text)
        self.assertTrue(self.client.get("/api/agents/wren").json()["agent"]["interactive"])
        # flipped later by a human, or by the session itself
        self.assertEqual(self.client.put("/api/agents/wren/interactive", headers=self.bot,
                                         json={"interactive": True}).status_code, 403)
        self.assertEqual(self.client.put("/api/agents/wren/interactive", headers=me,
                                         json={"interactive": False}).status_code, 200)
        self.assertEqual(self.claim(tid, me).status_code, 200)
        self.assertEqual(self.client.put("/api/agents/wren/interactive", headers=self.human,
                                         json={"interactive": True}).status_code, 200)
        self.assertIn('&quot;interactive&quot;: true', self.client.get("/onboard").text)

    def test_the_roster_carries_the_role(self):
        self.report("lena", "waiting")
        self.add("lena", profile="artist", channels=[self.art])
        self.register("lena-agent", "claude-code", "sg")      # the handle registers when the bot starts
        rows = {a["handle"]: a for a in self.client.get("/api/agents", headers=self.bot).json()["agents"]}
        self.assertIsNone(rows["edie"]["role"])
        # the fleet row for lena exists before her agent does; once registered, the role shows
        self.register("lena", "claude-code", "sg")
        rows = {a["handle"]: a for a in self.client.get("/api/agents", headers=self.bot).json()["agents"]}
        self.assertEqual(rows["lena"]["profile"], "artist")
        self.assertTrue(rows["lena"]["role"].startswith("A visual artist"))


class Supervision(Base):
    def workspace(self):
        return self.client.get("/api/workspaces/sg", headers=self.human).json()["workspace"]["supervisor"]

    def test_a_workspace_knows_its_supervisor(self):
        # one bot: nobody is expected to supervise yet
        self.assertEqual(self.workspace(), {"handle": None, "running": False, "wanted": False})
        for h in ("jonas", "nico"):
            self.register(h, "claude-code", "sg")
        self.assertEqual(self.workspace(), {"handle": None, "running": False, "wanted": True})
        listed = next(w for w in self.client.get("/api/workspaces", headers=self.human).json()["workspaces"]
                      if w["slug"] == "sg")
        self.assertEqual(listed["supervisor"]["wanted"], True)
        # a bot added with the supervisor profile, or a clone of it, is the supervisor
        r = self.client.post("/api/profiles", headers=self.human,
                             json={"name": "sg-supervisor", "role": "keeps sg moving", "from": "supervisor"})
        self.assertEqual(r.status_code, 201, r.text)
        self.report("sven", "waiting")
        self.add("sven", profile="sg-supervisor")
        self.register("sven", "claude-code", "sg")
        self.assertEqual(self.workspace(), {"handle": "sven", "running": True, "wanted": True})
        # stopped from the fleet page: still named, not running
        r = self.client.put("/api/fleet/bots/sven", headers=self.human,
                            json={"host": "box", "run": False, "harness": "claude"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.workspace(), {"handle": "sven", "running": False, "wanted": True})

    def test_the_pages_say_so(self):
        page = self.client.get("/workspaces/sg").text
        self.assertIn("No supervisor here.", page)
        self.assertIn("Add a bot with the supervisor profile", page)
        self.assertIn("supervisorFact(w)", page)
        readme = (ROOT / "README.md").read_text()
        self.assertIn("### Supervisor", readme)
        self.assertIn("| Supervisor | The bot that owns progress in a workspace |", readme)


class KeepingWorkMoving(Base):
    def setUp(self):
        super().setUp()
        self.report("edie", "running sg")
        self.add("edie")
        self.tid = self.post("build the thing")["id"]
        self.assertEqual(self.claim(self.tid).status_code, 200)
        self.post("on it: branch edie-thing, PR #12 soon", auth=self.bot, reply=self.tid)

    def test_a_running_worker_is_left_alone(self):
        self.assertEqual(progress.sweep(), {"released": [], "nudged": []})
        self.assertEqual(self.worker(self.tid), "edie")

    def test_a_stopped_worker_is_released_with_where_it_stood(self):
        self.report("edie", "stopped")      # crashed, or stopped for good: the keeper says down...
        self.assertEqual(progress.sweep(), {"released": [], "nudged": []})     # ...a restart gets a grace period
        self.silent("edie")                 # ...and the bot has not touched the board since
        done = progress.sweep()
        self.assertEqual(done["released"], [self.tid])
        self.assertIsNone(self.worker(self.tid))
        notice = self.messages(self.tid)[-1]
        self.assertEqual(notice["author"], "board")
        self.assertEqual(notice["meta"], {"kind": "thread_released", "by": "board", "was": "edie", "why": "stopped"})
        self.assertIn("@edie is stopped; available for pickup.", notice["body"])
        self.assertIn("Last from @edie", notice["body"])
        self.assertIn("branch edie-thing, PR #12 soon", notice["body"])
        self.assertEqual(progress.sweep(), {"released": [], "nudged": []})     # once
        # the next eligible bot claims it; a bot not in the fleet, unseen for an hour, counts as away
        other = self.register("nico", "claude-code", "sg")
        self.assertEqual(self.claim(self.tid, other).status_code, 200)
        self.assertEqual(progress.sweep(), {"released": [], "nudged": []})     # seen just now
        self.silent("nico")
        self.assertEqual(progress.sweep()["released"], [self.tid])
        self.assertIn("has not been seen for a while", self.messages(self.tid)[-1]["body"])

    def test_a_human_reply_into_a_dead_thread_frees_it_at_once(self):
        self.report("edie", "stopped")
        self.silent("edie")
        self.post("@edie what is the status?", reply=self.tid)
        self.assertIsNone(self.worker(self.tid))
        bodies = [m["body"] for m in self.messages(self.tid)]
        self.assertIn("what is the status?", bodies[-2])
        self.assertTrue(bodies[-1].startswith("Thread worker released by the board: @edie is stopped"))
        # a worker that is running gets the reply as usual, nothing is released
        self.assertEqual(self.claim(self.tid).status_code, 200)      # the claim touches the board: seen now
        self.report("edie", "running sg")
        self.post("and now?", reply=self.tid)
        self.assertEqual(self.worker(self.tid), "edie")
        # a box that stopped reporting: presence decides, after an hour
        db.conn().execute("DELETE FROM fleet_hosts"); db.conn().commit()
        self.post("anyone?", reply=self.tid)
        self.assertEqual(self.worker(self.tid), "edie")
        self.silent("edie")
        self.post("anyone at all?", reply=self.tid)
        self.assertIsNone(self.worker(self.tid))
        self.assertIn("has not been seen for a while", self.messages(self.tid)[-1]["body"])

    def test_a_quiet_worker_is_nudged_once_then_freed(self):
        db.conn().execute("UPDATE messages SET created_at=? WHERE thread_id=?", (LONG_AGO, self.tid))
        db.conn().commit()
        done = progress.sweep()
        self.assertEqual(done, {"released": [], "nudged": [self.tid]})
        nudge = self.messages(self.tid)[-1]
        self.assertEqual(nudge["author"], "board")
        self.assertTrue(nudge["body"].startswith("@edie this thread has not moved in"))
        self.assertEqual(nudge["mentions"], ["edie"])
        self.assertEqual(nudge["meta"], {"kind": "nudge", "to": "edie"})
        self.assertEqual(self.worker(self.tid), "edie")
        self.assertEqual(progress.sweep(), {"released": [], "nudged": []})     # not twice
        # the worker answers: the clock restarts, nothing is freed
        self.post("next step: tests, then the PR", auth=self.bot, reply=self.tid)
        self.assertEqual(progress.sweep(), {"released": [], "nudged": []})
        # silence after a nudge frees the thread
        db.conn().execute("UPDATE messages SET created_at=? WHERE thread_id=?", (LONG_AGO, self.tid))
        db.conn().commit()
        progress.sweep()
        db.conn().execute("UPDATE messages SET created_at=? WHERE thread_id=?", (LONG_AGO, self.tid))
        db.conn().commit()
        self.assertEqual(progress.sweep()["released"], [self.tid])
        self.assertIn("did not answer the nudge", self.messages(self.tid)[-1]["body"])
        self.assertIsNone(self.worker(self.tid))

    def test_completed_threads_are_not_touched(self):
        last = self.messages(self.tid)[-1]["id"]
        self.client.post(f"/api/threads/{self.tid}/complete", headers=self.bot,
                         json={"through": last}).raise_for_status()
        self.report("edie", "stopped")
        self.silent("edie")
        self.assertEqual(progress.sweep(), {"released": [], "nudged": []})
        self.assertEqual(self.worker(self.tid), "edie")


if __name__ == "__main__":
    unittest.main()
