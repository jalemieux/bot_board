"""Host bots (#595) and where a bot runs (#875), the board side: a bot can run outside its container, as the operator, on any
box and for a new bot or an existing one. Only a human sets it; on a box that chose containers it needs the box's switch and a
reason; the box's keeper is told to run it on the host only while all of that holds. Run with python -m unittest discover -s tests."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main


class HostBots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register("op", "human")
        self.bot = self.register("edie", "claude-code")
        self.keepers = {"mac": self.register("keeper-mac", "keeper"), "linux": self.register("keeper-linux", "keeper")}
        self.client.post("/api/workspaces", headers=self.human, json={"slug": "proj", "name": "Proj", "purpose": "x"})
        self.report("mac", "Darwin 25.0")
        self.report("linux", "Linux 6.8")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post("/api/agents", json={"handle": handle, "kind": kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": "Bearer " + r.json()["token"]}

    OS = {"mac": "Darwin 25.0", "linux": "Linux 6.8"}

    def report(self, box, os_name=None, bots=()):
        body = {"bots": list(bots), "os": os_name or self.OS[box]}      # a keeper reports its OS every time
        r = self.client.put(f"/api/fleet/hosts/{box}", headers=self.keepers[box], json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return {w["handle"]: w for w in r.json()["want"]}

    def switch(self, box, on, auth=None, expected=200):
        r = self.client.put(f"/api/fleet/hosts/{box}/host-bots", headers=auth or self.human, json={"on": on})
        self.assertEqual(r.status_code, expected, r.text)
        return r

    def make(self, handle="mira", box="mac", expected=200, **extra):
        body = {"host": box, "run": True, "harness": "claude", "workspace": "proj", "host_reason": "Godot captures", **extra}
        r = self.client.put(f"/api/fleet/bots/{handle}", headers=self.human, json=body)
        self.assertEqual(r.status_code, expected, r.text)
        return r

    def test_only_a_human_turns_the_switch_on_any_box(self):
        self.switch("mac", True, auth=self.bot, expected=403)
        self.switch("mac", True, auth=self.keepers["mac"], expected=403)
        self.assertIn("no box named", self.switch("nowhere", True, expected=409).json()["error"])
        self.switch("mac", True)
        self.switch("linux", True)          # not Mac only (#875)
        hosts = {h["name"]: h for h in self.client.get("/api/fleet").json()["hosts"]}
        self.assertEqual((hosts["mac"]["host_bots"], hosts["linux"]["host_bots"]), (True, True), "the switch is on any operating system (#877)")

    def test_a_box_with_no_container_runtime_has_no_switch_to_show(self):
        # every bot there runs on the machine: nothing to allow (#877)
        self.client.put("/api/fleet/hosts/linux", headers=self.keepers["linux"], json={
            "bots": [], "os": "Linux 6.8", "runtime": {"setting": "host", "state": "ok"}}).raise_for_status()
        self.client.put("/api/fleet/hosts/mac", headers=self.keepers["mac"], json={
            "bots": [], "os": "Darwin 25.0", "runtime": {"setting": "docker", "state": "ok"}}).raise_for_status()
        hosts = {h["name"]: h for h in self.client.get("/api/fleet").json()["hosts"]}
        self.assertEqual((hosts["linux"]["host_bots"], hosts["mac"]["host_bots"]), (None, False))

    def test_a_host_bot_needs_the_switch_and_a_reason_on_any_box_and_may_be_an_existing_bot(self):
        why = lambda r: r.json()["error"]
        self.assertIn("off on mac", why(self.make(expected=409)))
        self.assertIn("off on linux", why(self.make(box="linux", expected=409)), "not Mac only")
        self.switch("mac", True)
        self.switch("linux", True)
        self.assertIn("why the bot needs the host", why(self.make(host_reason="  ", expected=409)))
        self.assertEqual(self.client.put("/api/fleet/bots/mira", headers=self.bot,
                                         json={"host": "mac", "run": True, "host_reason": "x"}).status_code, 403)
        self.make()                                              # all conditions met
        self.make(handle="penn", box="linux")                    # on a Linux box too
        self.report("mac", bots=[{"handle": "edie", "harness": "claude", "status": "waiting", "serves": "/pool", "pool": True}])
        self.make(handle="edie")                                 # an existing bot is converted (#875)
        self.assertTrue(self.report("mac")["edie"]["host_flag"])

    def test_the_keeper_is_told_to_run_it_on_the_host_only_while_everything_holds(self):
        self.switch("mac", True)
        self.make(host_apps=["Godot", "Blender"])
        row = self.report("mac")["mira"]
        self.assertTrue(row["on_host"])
        self.assertEqual((row["host_reason"], row["host_apps"], row["host_by"]), ("Godot captures", ["Godot", "Blender"], "op"))
        self.assertEqual(row["host"], "mac", "host is still the box it runs on")
        self.switch("mac", False)
        self.assertFalse(self.report("mac")["mira"]["on_host"], "the switch off: back in a container")
        self.assertTrue(self.report("mac")["mira"]["host_flag"], "but the bot keeps its flag")
        self.switch("mac", True)
        self.assertTrue(self.report("mac")["mira"]["on_host"], "and the switch on brings it back")
        self.assertTrue(self.report("mac", os_name="Linux 6.8")["mira"]["on_host"], "not Mac only: the box's OS does not matter")
        # moved to another box (a pool move): it is no longer the box it was made for
        r = self.client.put("/api/fleet/bots/mira", headers=self.human, json={"host": "linux", "run": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.report("linux")["mira"]["on_host"], "a host bot on another box runs in a container")

    def test_a_bot_is_told_by_the_board_whether_it_runs_on_the_host(self):
        self.switch("mac", True)
        r = self.client.post("/api/agents", json={"handle": "mira", "kind": "claude-code"})   # the bot registers itself later
        self.assertEqual(r.status_code, 201)
        self.make()                       # an agent that exists may be set to run on the host too (#875)
        fresh = self.make(handle="zed")
        self.report("mac")
        tok = self.client.post("/api/agents", json={"handle": "zed", "kind": "claude-code"})
        self.assertEqual(tok.status_code, 201, tok.text)
        zed = {"Authorization": "Bearer " + tok.json()["token"]}
        me = self.client.get("/api/me", headers=zed).json()
        self.assertEqual((me["host_bot"]["box"], me["host_bot"]["reason"], me["host_bot"]["by"]), ("mac", "Godot captures", "op"))
        self.assertIsNone(self.client.get("/api/me", headers=self.bot).json()["host_bot"], "an ordinary bot is told nothing")
        self.switch("mac", False)
        self.assertIsNone(self.client.get("/api/me", headers=zed).json()["host_bot"], "the switch off: the board no longer says so")

    def test_a_human_edits_a_host_bots_reason_and_apps_and_moves_it_back(self):
        self.switch("mac", True)
        self.make()
        put = lambda auth, **b: self.client.put("/api/fleet/bots/mira/host", headers=auth, json=b)
        self.assertEqual(put(self.bot, reason="x").status_code, 403)
        self.assertEqual(put(self.human, reason="Blender renders", apps=["Blender"]).status_code, 200)
        row = self.report("mac")["mira"]
        self.assertEqual((row["host_reason"], row["host_apps"]), ("Blender renders", ["Blender"]))
        self.assertEqual(put(self.human, reason="  ").status_code, 409)
        self.assertEqual(put(self.human, reason="Changed", apps=["x" * 65]).status_code, 409)
        self.assertEqual(self.report("mac")["mira"]["host_reason"], "Blender renders", "a refused edit changes nothing")
        self.assertEqual(self.client.put("/api/fleet/bots/edie/host", headers=self.human, json={"reason": "x"}).status_code, 409,
                         "an ordinary bot cannot be given host details")
        self.assertEqual(self.client.delete("/api/fleet/bots/mira/host", headers=self.bot).status_code, 403)
        self.assertEqual(self.client.delete("/api/fleet/bots/mira/host", headers=self.human).status_code, 200)
        row = self.report("mac")["mira"]
        self.assertEqual((row["host_flag"], row["on_host"]), (False, False), "moved back: a container from its next start")
        self.assertEqual(self.client.delete("/api/fleet/bots/mira/host", headers=self.human).status_code, 409)

    def test_the_roster_names_who_made_a_bot_a_host_bot_only_while_it_runs_on_the_host(self):
        self.switch("mac", True)
        self.make()
        by = lambda: {a["handle"]: a["host_by"] for a in self.client.get("/api/agents").json()["agents"]}
        self.assertEqual((by().get("mira"), by()["edie"], by()["op"]), (None, None, None), "not an agent yet: nobody is marked")
        self.client.post("/api/agents", json={"handle": "mira", "kind": "claude-code"}).raise_for_status()
        self.assertEqual((by()["mira"], by()["edie"], by()["op"]), ("op", None, None))
        self.switch("mac", False)
        self.assertIsNone(by()["mira"], "the switch off, and no keeper says it runs on the host: not marked")
        self.report("mac", bots=[{"handle": "mira", "harness": "claude", "status": "waiting", "serves": "/pool", "pool": True, "runmode": "host"}])
        self.assertEqual(by()["mira"], "op", "a bot its box reports running on the host stays marked after the switch goes off")
        self.client.delete("/api/fleet/bots/mira/host", headers=self.human).raise_for_status()
        self.assertEqual(by()["mira"], "op", "and after Move back, until it restarts")
        self.report("mac", bots=[{"handle": "mira", "harness": "claude", "status": "waiting", "serves": "/pool", "pool": True, "runmode": "container"}])
        self.assertIsNone(by()["mira"], "once it runs in a container again: not marked")

    def test_an_ordinary_bot_is_never_on_the_host(self):
        self.switch("mac", True)
        r = self.client.put("/api/fleet/bots/plain", headers=self.human,
                            json={"host": "mac", "run": True, "harness": "claude", "workspace": "proj"})
        self.assertEqual(r.status_code, 200, r.text)
        row = self.report("mac")["plain"]
        self.assertEqual((row["on_host"], row["host_flag"]), (False, False))


if __name__ == "__main__":
    unittest.main()
