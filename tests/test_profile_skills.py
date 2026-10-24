"""PR 3 of the skill registry: a launcher installs its profile's sourced skills when it
starts and records when its session began; the keeper reports that and the box's leftover
BOT_SKILLS; the fleet page says which of those are not on the board yet.
Run with python -m unittest discover -s tests."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))
from fakes import harness as fh                  # noqa: E402
from fakes.board import FakeBoard                # noqa: E402
from test_launcher_e2e import Launcher           # noqa: E402
from test_fleet_kill import load_keeper          # noqa: E402
from fastapi.testclient import TestClient        # noqa: E402
from app import db, fleet, main, registry        # noqa: E402

BOT = str(ROOT / "bin/bot")


def skill_dir(root: Path, name: str) -> str:
    d = root / "sources" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Does {name}.\n---\n\nBody.\n")
    return str(d)


class LauncherInstallsProfileSkills(unittest.TestCase):
    def test_sourced_skills_are_linked_at_start_and_the_session_start_is_recorded(self):
        with FakeBoard() as board:
            L = Launcher(board, "claude")
            try:
                home = Path(L.tmp.name)
                src = skill_dir(home, "hello")
                board.profile = {"profile": {"name": "dev"}, "skills": [
                    {"name": "hello", "kind": "github", "source": src, "ref": "main", "mandatory": False},
                    {"name": "written", "kind": "authored", "source": "", "ref": "", "mandatory": True},
                    {"name": "gone", "kind": "github", "source": str(home / "sources" / "gone"), "ref": "main", "mandatory": False}]}
                L.plan(fh.step("claude", "ok", "read the rules"))
                L.start()
                L.wait_for(lambda: len(L.prompts()) >= 1 and L.read("session-started"), "the first turn and session-started")
                self.assertRegex(L.read("session-started"), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
                self.assertEqual(L.read("protocol"), "2")
                # the github-kind skill is linked where the harnesses load skills from; the authored one is not
                for base in (".claude/skills", ".agents/skills"):
                    link = home / base / "hello"
                    self.assertTrue(link.is_symlink(), f"{link} not linked")
                    self.assertEqual(os.path.realpath(link), os.path.realpath(src))
                    self.assertFalse((home / base / "written").exists())
                self.assertEqual(L.read("skills").split(), [src])      # what keeps its clone from gc
                log = L.log()
                self.assertIn("skill hello: linked", log)
                self.assertIn("skill " + str(home / "sources" / "gone") + ": no directory", log)
                L.stop()
            finally:
                L.close()

    def test_a_bot_without_a_profile_links_nothing(self):
        with FakeBoard() as board:
            L = Launcher(board, "claude")
            try:
                L.plan(fh.step("claude", "ok", "read the rules"))
                L.start()
                L.wait_for(lambda: len(L.prompts()) >= 1, "the first turn")
                self.assertEqual(L.read("skills"), "")
                self.assertFalse((Path(L.tmp.name) / ".claude/skills").exists())
                L.stop()
            finally:
                L.close()


class KeeperReports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = os.environ.get("HOME"), os.environ.get("BOT_CONFIG")
        self.k = load_keeper(self.tmp.name)

    def tearDown(self):
        for k, v in zip(("HOME", "BOT_CONFIG"), self.saved):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_session_started_and_bot_skills_are_reported(self):
        state = Path(self.k.CFG) / "bots" / "ann"
        state.mkdir(parents=True)
        (state / "session-started").write_text("2026-10-24T10:00:00Z\n")
        self.k.out = lambda *cmd: "ann\tclaude\twaiting\t1\t\t\t\t\n" if cmd[1:2] == ("ps",) else ""
        self.k.git = lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "")
        self.assertEqual(self.k.bots()[0]["session_started"], "2026-10-24T10:00:00Z")
        cfg = Path(self.tmp.name) / "config"
        cfg.write_text('BOARD=http://x\nBOT_SKILLS="https://github.com/a/b/tree/main/skills/x /tmp/local-skill"  # two\n')
        os.environ["BOT_CONFIG"] = str(cfg)
        snap = self.k.snapshot()
        self.assertEqual(snap["bot_skills"], ["https://github.com/a/b/tree/main/skills/x", "/tmp/local-skill"])
        cfg.write_text("BOARD=http://x\n")
        self.assertEqual(self.k.snapshot()["bot_skills"], [])


class BoardSide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        r = self.client.post("/api/agents", json={"handle": "keeper-box", "kind": "keeper"})
        self.keeper = {"Authorization": "Bearer " + r.json()["token"]}
        r = self.client.post("/api/agents", json={"handle": "op", "kind": "human"})
        self.human = {"Authorization": "Bearer " + r.json()["token"]}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.tmp.cleanup()

    def put(self, report):
        r = self.client.put("/api/fleet/hosts/box", headers=self.keeper, json=report)
        self.assertEqual(r.status_code, 200, r.text)
        return fleet.hosts()[0]

    def test_the_report_keeps_the_new_fields_and_the_page_says_what_is_missing(self):
        real = registry.fetch_skill
        registry.fetch_skill = lambda s, r: {"text": "---\nname: sk-x\ndescription: X.\n---\n\nBody.\n", "commit": "abc"}
        try:
            self.client.put("/api/skills/sk-x", headers=self.human,
                            json={"fields": {"source": "https://github.com/a/b/tree/main/skills/x"}, "base_revision": 0}).raise_for_status()
        finally:
            registry.fetch_skill = real
        h = self.put({"bots": [{"handle": "ann", "status": "waiting", "harness": "claude", "protocol": 1,
                                "session_started": "2026-10-24T10:00:00Z"}],
                      "bot_skills": ["https://github.com/a/b/tree/main/skills/x", "https://github.com/a/b/tree/main/skills/y",
                                     "/home/me/skills/local"]})
        self.assertEqual(h["bots"][0]["session_started"], "2026-10-24T10:00:00Z")
        self.assertEqual(len(h["bot_skills"]), 3)
        self.assertEqual(registry.unregistered(h["bot_skills"]),
                         [{"source": "https://github.com/a/b/tree/main/skills/y", "addable": True},
                          {"source": "/home/me/skills/local", "addable": False}])
        page = self.client.get("/fleet").text
        self.assertIn("box still lists skills in its config", page)
        self.assertIn("still lists 3 skills", page)
        self.assertIn("1 of 3 is on the board already", page)
        self.assertIn("A local directory cannot be added from here", page)
        self.assertIn('class="add-skills" data-sources="[&quot;https://github.com/a/b/tree/main/skills/y&quot;]"', page)
        self.assertIn("Add the 1 missing skill to the board", page)
        # the launcher from before protocol 2 is noted in Versions
        self.assertIn("predates profile skills (protocol 2)", page)
        # once every source is on the board, the alert says so and offers no button
        real = registry.fetch_skill
        registry.fetch_skill = lambda s, r: {"text": "---\nname: sk-y\ndescription: Y.\n---\n\nBody.\n", "commit": "abc"}
        try:
            self.client.put("/api/skills/sk-y", headers=self.human,
                            json={"fields": {"source": "https://github.com/a/b/tree/main/skills/y"}, "base_revision": 0}).raise_for_status()
        finally:
            registry.fetch_skill = real
        h = self.put({"bots": [], "bot_skills": ["https://github.com/a/b/tree/main/skills/x", "https://github.com/a/b/tree/main/skills/y"]})
        page = self.client.get("/fleet").text
        self.assertIn("All 2 are on the board already", page)
        self.assertNotIn('class="add-skills"', page)
        # and a box with no BOT_SKILLS raises nothing
        self.put({"bots": []})
        self.assertNotIn("still lists skills", self.client.get("/fleet").text)


if __name__ == "__main__":
    unittest.main()
