"""The hardening from the security review (board thread 7566) that keeps the single-admin model:
private reads cover the pages, a bot's reads and replies stay in its workspace, posts are bounded,
the pages refuse to be framed, routine actions bind to the version read.
Run with python -m unittest discover -s tests."""
import base64
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from PIL import Image
from app import db, main, routines

CHECK = '#!/usr/bin/env bash\necho \'{"work": false}\'\n'
INIT_RE = re.compile(r"<script>window.__INIT__=(.*?);</script>", re.S)


def png() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


class Hardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        self.reads = patch.object(main, "READ_TOKEN_REQUIRED", False)
        self.reads.start()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register("jac", "human")
        for slug in ("game", "other"):
            self.call("POST", "/api/workspaces", {"slug": slug, "name": slug.title(), "purpose": "x"},
                      auth=self.human, expected=201)
        self.ada = self.register("ada", "claude-code", "game")
        self.milo = self.register("milo", "codex", "other")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.reads.stop()
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind, workspace=None):
        body = {"handle": handle, "kind": kind, **({"workspace": workspace} if workspace else {})}
        r = self.client.post("/api/agents", json=body)
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": "Bearer " + r.json()["token"]}

    def call(self, method, path, data=None, auth=None, expected=200, **kw):
        r = self.client.request(method, path, json=data, headers=auth, **kw)
        self.assertEqual(r.status_code, expected, r.text)
        return r.json()

    def post(self, auth, body, channel=None, expected=201, **fields):
        data = {"body": body, **({"channel": channel} if channel else {}), **fields}
        return self.call("POST", "/api/messages", data, auth=auth, expected=expected)

    # ------------------------------------------------------------ workspace scope on reads

    def test_attachments_are_read_only_inside_their_workspace(self):
        text = self.post(self.ada, "notes", "game", text_attachment="the plan")["message"]
        image = self.post(self.ada, "shot", "game", image_attachment=png())["message"]
        for m in (text, image):
            url = m["attachments"][0]["url"]
            self.assertEqual(self.client.get(url, headers=self.milo).status_code, 404)
            self.assertEqual(self.client.get(url, headers=self.ada).status_code, 200)
            self.assertEqual(self.client.get(url, headers=self.human).status_code, 200)
            self.assertEqual(self.client.get(url).status_code, 200)       # an open board reads without a token
        self.assertEqual(self.client.get(text["attachments"][0]["url"], headers=self.ada).text, "the plan")

    def test_an_agents_recent_messages_and_the_boards_list_are_scoped(self):
        self.post(self.human, "for the game", "game")
        self.post(self.human, "for the others", "other")
        seen = lambda auth: {m["channel"] for m in self.call("GET", "/api/agents/jac", auth=auth)["recent"]}
        self.assertEqual(seen(self.ada), {"game"})
        self.assertTrue({"game", "other"} <= seen(self.human))
        boards = lambda auth: {b["slug"] for b in self.call("GET", "/api/boards", auth=auth)["boards"]}
        self.assertEqual(boards(self.ada), {"game"})
        self.assertTrue({"game", "other"} <= boards(self.human))

    def test_a_reply_lands_in_its_own_threads_channel(self):
        root = self.post(self.human, "a thread in the game", "game")["message"]
        r = self.post(self.human, "smuggled", "other", reply_to=root["id"], expected=422)
        self.assertIn("is in #game, not #other", r["error"])
        self.assertEqual(self.post(self.human, "fine", "game", reply_to=root["id"])["message"]["thread_id"], root["id"])
        self.assertEqual(self.post(self.human, "also fine", reply_to=root["id"])["message"]["channel"], "game")
        self.post(self.milo, "from another workspace", reply_to=root["id"], expected=403)
        self.post(self.milo, "from another workspace", "other", reply_to=root["id"], expected=422)

    # ------------------------------------------------------------ bounded posts

    def test_tags_and_meta_are_bounded(self):
        self.post(self.human, "ok", "game", tags=["t%d" % i for i in range(main.MAX_TAGS)])
        self.post(self.human, "too many", "game", tags=["t%d" % i for i in range(main.MAX_TAGS + 1)], expected=422)
        self.post(self.human, "too long", "game", tags=["x" * (main.MAX_TAG_CHARS + 1)], expected=422)
        self.post(self.human, "ok", "game", meta={"needs": "jac", "ask": "a word"})
        self.post(self.human, "too big", "game", meta={"blob": "x" * main.MAX_META}, expected=422)

    def test_sign_ins_to_one_human_are_capped(self):
        tokens = [self.client.post("/api/agents", json={"handle": "jac", "kind": "human"}).json()["token"]
                  for _ in range(db.SIGN_INS_KEPT + 5)]
        count = db.conn().execute("SELECT COUNT(*) FROM agent_tokens").fetchone()[0]
        self.assertEqual(count, db.SIGN_INS_KEPT)
        self.assertEqual(self.client.get("/api/me", headers={"Authorization": "Bearer " + tokens[0]}).status_code, 401)
        self.assertEqual(self.client.get("/api/me", headers={"Authorization": "Bearer " + tokens[-1]}).status_code, 200)
        self.assertEqual(self.client.get("/api/me", headers=self.human).status_code, 200)   # the first token stays

    # ------------------------------------------------------------ the pages

    def init(self, page: str) -> dict:
        self.assertNotIn("<", INIT_RE.search(page).group(1))
        return json.loads(INIT_RE.search(page).group(1))

    def test_a_private_board_keeps_its_pages_too(self):
        msg = self.post(self.human, "secret plan", "game")["message"]
        cookie = {"Cookie": "bb_token=" + self.human["Authorization"][7:]}
        with patch.object(main, "READ_TOKEN_REQUIRED", True):
            for path in ("/search?q=secret", "/a/jac", "/fleet", "/routines", "/workspaces", "/admin"):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 401, path)
                self.assertIn("Sign in first", r.text)
                self.assertNotIn("secret plan", r.text)
                self.assertEqual(self.client.get(path, headers={"Cookie": "bb_token=bb_wrong"}).status_code, 401, path)
                self.assertEqual(self.client.get(path, headers=cookie).status_code, 200, path)
                self.assertEqual(self.client.get(path, headers=self.human).status_code, 200, path)
            self.assertIn("secret plan", self.client.get("/search?q=secret", headers=cookie).text)
            # the board page opens, empty, behind its sign-in; and fills in once the reader is known
            for path in ("/", "/c/game", f"/t/{msg['id']}", "/unread"):
                page = self.client.get(path)
                self.assertEqual(page.status_code, 200, path)
                init = self.init(page.text)
                self.assertTrue(init["private"])
                self.assertEqual((init["channels"], init["conversations"], init["agents"]), ([], [], []))
                self.assertNotIn("jac", json.dumps(init["agents"]))
            init = self.init(self.client.get("/", headers=cookie).text)
            self.assertFalse(init["private"])
            self.assertIn("game", [c["slug"] for c in init["channels"]])
            # the cookie is for pages only: /api still wants its bearer token
            self.assertEqual(self.client.get("/api/messages", headers=cookie).status_code, 401)
            self.assertEqual(self.client.get(f"/api/messages/{msg['id']}", headers=cookie).status_code, 401)
        # an open board ignores the cookie too, and has nothing to gate
        self.assertFalse(self.init(self.client.get("/").text)["private"])
        self.assertEqual(self.client.get("/search?q=secret").status_code, 200)

    def test_pages_refuse_to_be_framed(self):
        for path in ("/", "/fleet", "/search?q=x", "/c/no-such-channel"):
            r = self.client.get(path)
            self.assertEqual(r.headers.get("x-frame-options"), "DENY", path)
            self.assertIn("frame-ancestors 'none'", r.headers.get("content-security-policy", ""), path)
            self.assertEqual(r.headers.get("x-content-type-options"), "nosniff", path)
        r = self.client.get("/api/stats")
        self.assertNotIn("x-frame-options", r.headers)

    def test_board_text_cannot_close_the_embedded_script(self):
        evil = "</script><img src=x onerror=alert(1)>"
        self.client.post("/api/agents", json={"handle": "bad", "kind": "claude-code", "description": evil})
        self.call("POST", "/api/channels", {"slug": "tricky", "topic": evil}, auth=self.human, expected=201)
        page = self.client.get("/").text
        self.assertNotIn(evil, page)
        init = self.init(page)
        self.assertEqual([a["description"] for a in init["agents"] if a["handle"] == "bad"], [evil])

    # ------------------------------------------------------------ routines

    def test_a_changed_routine_drops_its_pending_test_and_acts_on_the_version_read(self):
        runner = self.register(routines.cli.ME, "cron")
        data = {"name": "triage", "channel": "game", "check": CHECK, "prompt": "Triage.", "every": 60}
        r = self.call("POST", "/api/routines", data, auth=self.human, expected=201)
        path = f"/api/routines/{r['id']}"
        self.assertTrue(self.call("POST", path + "/test", {}, auth=self.human)["test_pending"])
        v2 = self.call("PATCH", path, {"every": 300}, auth=self.human)
        self.assertEqual((v2["version"], v2["test_pending"], v2["test_asked"]), (2, False, 0))
        # a test the runner did run stays the last one; only the ask past it goes with the old version
        tested = self.call("POST", path + "/test", {}, auth=self.human)
        self.call("POST", path + "/report", {"test": {"for": tested["test_asked"], "version": 2, "ok": True, "work": False}},
                  auth=runner)
        self.assertTrue(self.call("POST", path + "/test", {}, auth=self.human)["test_pending"])
        v3 = self.call("PATCH", path, {"every": 600}, auth=self.human)
        self.assertEqual((v3["version"], v3["test_pending"], v3["test_asked"]), (3, False, tested["test_asked"]))
        self.assertEqual(v3["last_test"]["version"], 2)
        # an action acts on the version it read, or not at all
        real = routines._row
        with patch.object(routines, "_row", lambda rid: {**real(rid), "version": real(rid)["version"] - 1}):
            self.assertIn("changed while", self.call("POST", path + "/pause", {}, auth=self.human, expected=422)["error"])
        self.assertEqual(self.call("GET", path, auth=self.human)["version"], 3)


if __name__ == "__main__":
    unittest.main()
