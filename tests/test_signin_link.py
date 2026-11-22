"""#653: the installer opens the board already signed in, through a one-time link the board issues to a
human's token. Run with python -m unittest discover -s tests."""
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main


class SigninLink(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(db, "DB_PATH", os.path.join(tmp, "board.db")))
        self.stack.enter_context(patch.object(db, "_local", threading.local()))
        self.client = self.stack.enter_context(TestClient(main.app))
        self.addCleanup(lambda: hasattr(db._local, "conn") and db._local.conn.close())
        main._signin_links.clear()
        r = self.client.post("/api/agents", json={"handle": "sam", "kind": "human"})
        self.token = r.json()["token"]
        self.auth = {"Authorization": "Bearer " + self.token}

    def link(self):
        r = self.client.post("/api/signin-links", headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["path"]

    def test_the_link_signs_a_browser_in_once(self):
        path = self.link()
        self.assertTrue(path.startswith("/signin/"))
        code = path.rsplit("/", 1)[1]
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 200, "opening the page does not spend the link")
        r = self.client.post(f"/api/signin-links/{code}/redeem")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["handle"], "sam")
        me = self.client.get("/api/me", headers={"Authorization": "Bearer " + r.json()["token"]})
        self.assertEqual(me.json()["agent"]["handle"], "sam")
        self.assertNotEqual(r.json()["token"], self.token, "the browser gets a token of its own")
        self.assertEqual(self.client.post(f"/api/signin-links/{code}/redeem").status_code, 410, "second use")

    def test_an_expired_link_is_refused(self):
        code = self.link().rsplit("/", 1)[1]
        with patch.object(time, "time", return_value=time.time() + main.SIGNIN_LINK_TTL + 1):
            self.assertEqual(self.client.post(f"/api/signin-links/{code}/redeem").status_code, 410)

    def test_only_a_known_human_gets_a_link(self):
        self.assertEqual(self.client.post("/api/signin-links").status_code, 401)
        bot = self.client.post("/api/agents", json={"handle": "robo", "kind": "claude-code"}).json()["token"]
        self.assertEqual(self.client.post("/api/signin-links", headers={"Authorization": "Bearer " + bot}).status_code, 403)
        self.assertEqual(self.client.post("/api/signin-links/nope/redeem").status_code, 410)

    def test_the_code_cannot_break_out_of_the_page_script(self):
        body = self.client.get("/signin/%3C%2Fscript%3E%3Cb%3E").text
        self.assertNotIn("</script><b>", body)


if __name__ == "__main__":
    unittest.main()
