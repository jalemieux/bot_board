"""#875: a bot carries `runs`, container or host, with its machine's setting as the default (docs/runtime-choice.md). The column,
its migration from the host bots of #595, the rule that replaces the Mac-only and new-bot-only one, the answer of GET /api/me, and
what changing an existing bot says. Run with python -m unittest discover -s tests."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, fleet, main


class RunsPerBot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register("op", "human")
        self.keepers = {n: self.register("keeper-" + n, "keeper") for n in ("docker", "bare", "old")}
        self.client.post("/api/workspaces", headers=self.human, json={"slug": "proj", "name": "Proj", "purpose": "x"})
        # a box that chose containers, one with no runtime (BOT_RUNTIME unset reports `host`), and a keeper that does not say
        self.report("docker")
        self.report("bare", {"setting": "host", "state": "ok", "detail": ""})
        self.report("old", None)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post("/api/agents", json={"handle": handle, "kind": kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": "Bearer " + r.json()["token"]}

    DOCKER = {"setting": "docker", "state": "ok", "detail": ""}

    def report(self, box, runtime=DOCKER, bots=()):
        body = {"bots": list(bots), "os": "Linux 6.8", **({"runtime": runtime} if runtime else {})}
        r = self.client.put(f"/api/fleet/hosts/{box}", headers=self.keepers[box], json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return {w["handle"]: w for w in r.json()["want"]}

    def put(self, handle, box="docker", expected=200, **extra):
        r = self.client.put(f"/api/fleet/bots/{handle}", headers=self.human,
                            json={"host": box, "run": True, "harness": "claude", "workspace": "proj", **extra})
        self.assertEqual(r.status_code, expected, r.text)
        return r

    def row(self, handle):
        return dict(db.conn().execute("SELECT * FROM fleet_bots WHERE handle=?", (handle,)).fetchone())

    def switch(self, box, on=True):
        self.client.put(f"/api/fleet/hosts/{box}/host-bots", headers=self.human, json={"on": on}).raise_for_status()

    def test_a_bot_with_no_setting_follows_its_machine_and_an_older_client_changes_nothing(self):
        self.put("plain")
        self.assertIsNone(self.row("plain")["runs"])
        self.assertIsNone(fleet.runs_of("plain"))
        self.assertEqual((fleet.runs_default("docker"), fleet.runs_default("bare"), fleet.runs_default("old")), ("container", "host", "container"))

    def test_a_host_bot_of_595_becomes_runs_host_with_its_reason_kept(self):
        self.put("mira", host_reason="Godot captures", host_apps=["Godot"], expected=409)      # the switch is off
        self.switch("docker")
        self.put("mira", host_reason="Godot captures", host_apps=["Godot"])
        c = db.conn()
        c.execute("UPDATE fleet_bots SET runs=NULL WHERE handle='mira'"); c.commit()         # as a row from before the column
        fleet.init()
        r = self.row("mira")
        self.assertEqual((r["runs"], r["onhost"], r["host_reason"]), ("host", "1", "Godot captures"))
        self.assertEqual(self.report("docker")["mira"]["runs"], "host", "the report to the keeper carries it")

    def test_container_needs_a_runtime_and_the_words_say_what_to_install(self):
        self.put("one", runs="container")
        self.assertEqual(self.row("one")["runs"], "container")
        why = self.put("two", box="bare", runs="container", expected=409).json()["error"]
        self.assertIn("no container runtime", why)
        self.assertIn("Docker Desktop on a Mac", why)
        self.put("three", box="old", runs="container")       # a keeper that says nothing: not refused

    def test_host_on_a_box_that_chose_containers_needs_the_switch_and_a_reason_and_on_a_box_with_none_nothing(self):
        err = lambda r: r.json()["error"]
        self.assertIn("off on docker", err(self.put("aa", runs="host", host_reason="why", expected=409)))
        self.switch("docker")
        self.assertIn("why the bot needs the host", err(self.put("aa", runs="host", expected=409)))
        self.assertIn("why the bot needs the host", err(self.put("aa", runs="host", host_reason=" ", expected=409)))
        self.assertIn("up to 20 names", err(self.put("aa", runs="host", host_reason="why", host_apps=["x" * 65], expected=409)))
        self.put("aa", runs="host", host_reason="needs Godot", host_apps=["Godot"])
        self.assertEqual((self.row("aa")["runs"], self.row("aa")["onhost"]), ("host", "1"))
        self.put("bb", box="bare", runs="host")               # no runtime: every bot runs on the host, so nothing to gate
        self.assertEqual((self.row("bb")["runs"], self.row("bb")["onhost"]), ("host", None))
        self.assertEqual(self.client.put("/api/fleet/bots/z", headers=self.keepers["docker"],
                                         json={"host": "docker", "run": True, "runs": "host", "host_reason": "x"}).status_code, 403)

    def test_an_existing_bot_may_be_changed_and_the_answer_says_it_starts_a_new_session(self):
        self.switch("docker")
        self.report("docker", {"setting": "docker", "state": "ok", "detail": ""},
                    bots=[{"handle": "ada", "harness": "claude", "status": "waiting", "serves": "/pool", "pool": True}])
        r = self.put("ada", runs="host", host_reason="needs Godot")
        self.assertIn("new harness session", r.json()["note"])
        self.assertTrue(self.row("ada")["restart"], "it restarts when idle, by the restart path that exists")
        r = self.put("ada", runs="host", host_reason="needs Godot")      # the same again: nothing changes, nothing to say
        self.assertNotIn("note", r.json())
        r = self.put("ada", runs="container")
        self.assertIn("note", r.json())
        self.assertEqual((self.row("ada")["runs"], self.row("ada")["onhost"]), ("container", None), "back in a container: the host flag goes")
        self.assertNotIn("note", self.put("fresh", runs="container").json(), "a new bot has no session to lose")

    def test_a_bot_is_told_where_it_runs_and_host_bot_is_still_answered_for_an_older_launcher(self):
        self.switch("docker")
        self.put("zed", runs="host", host_reason="needs Godot")
        self.report("docker")
        tok = self.client.post("/api/agents", json={"handle": "zed", "kind": "claude-code"}).json()["token"]
        me = self.client.get("/api/me", headers={"Authorization": "Bearer " + tok}).json()
        self.assertEqual(me["runs"], "host")
        self.assertEqual(me["host_bot"]["reason"], "needs Godot")
        other = self.client.post("/api/agents", json={"handle": "ben", "kind": "claude-code"}).json()["token"]
        me = self.client.get("/api/me", headers={"Authorization": "Bearer " + other}).json()
        self.assertEqual((me["runs"], me["host_bot"]), (None, None))

    def test_a_value_that_is_not_a_setting_is_refused_by_the_schema(self):
        self.assertEqual(self.client.put("/api/fleet/bots/x", headers=self.human,
                                         json={"host": "docker", "run": True, "harness": "claude", "workspace": "proj", "runs": "vm"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
