"""#214: the board page opens on "Sign in as..." when nobody is signed in, or when the
identity saved in this browser is refused by this board. Run with python -m unittest discover -s tests."""
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main


class SigninGate(unittest.TestCase):
    def setUp(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Install Playwright and Chromium for sign-in UI checks")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(db, "DB_PATH", os.path.join(tmp, "board.db")))
        self.stack.enter_context(patch.object(db, "_local", threading.local()))
        self.stack.enter_context(patch.object(main, "READ_TOKEN_REQUIRED", False))
        self.client = self.stack.enter_context(TestClient(main.app))
        self.addCleanup(lambda: hasattr(db._local, "conn") and db._local.conn.close())
        pw = self.stack.enter_context(sync_playwright())
        self.browser = pw.chromium.launch()
        self.stack.callback(self.browser.close)

    def page(self, identity=None, down=()):
        """A page on this board; `down`: paths whose requests fail as if the board were unreachable."""
        page = self.browser.new_page()
        def serve(route):
            path = route.request.url.replace("http://board.test", "")
            if "wait=" in path or path.split("?")[0] in down:
                return route.abort()
            r = self.client.request(route.request.method, path, content=route.request.post_data,
                                    headers=route.request.headers)
            route.fulfill(status=r.status_code, body=r.content, content_type=r.headers.get("content-type", "text/plain"))
        page.route("**/*", serve)
        if identity:
            page.add_init_script("localStorage.setItem('bb.me', " + json.dumps(json.dumps(identity)) + ")")
        page.goto("http://board.test/")
        page.wait_for_timeout(500)
        return page

    def gated(self, page):
        return page.eval_on_selector("#signin", "d => d.open && d.classList.contains('gate')")

    def test_a_new_board_waits_for_the_first_sign_in(self):
        page = self.page()
        self.assertTrue(self.gated(page))
        self.assertEqual(page.inner_text("#si-title"), "Sign in as…")
        self.assertTrue(page.is_visible("#si-first"), "a new board says what signing in sets up")
        self.assertFalse(page.is_visible("#si-cancel"))
        page.keyboard.press("Escape")
        self.assertTrue(self.gated(page), "Escape does not get past the gate")
        page.fill("#si-handle", "jac")
        page.click("#si-go")
        page.wait_for_function("!document.querySelector('#signin').open")
        self.assertEqual(page.inner_text("#whoami"), "posting as @jac")

    def test_a_signed_in_browser_sees_the_board(self):
        token = self.client.post("/api/agents", json={"handle": "jac", "kind": "human"}).json()["token"]
        page = self.page({"handle": "jac", "token": token})
        self.assertFalse(page.eval_on_selector("#signin", "d => d.open"))

    def test_an_identity_from_an_earlier_board_is_forgotten(self):
        page = self.page({"handle": "ghost", "token": "from-an-earlier-board"})
        self.assertTrue(self.gated(page))
        self.assertIn("not valid on this board", page.inner_text("#si-err"))
        self.assertIsNone(page.evaluate("JSON.parse(localStorage.getItem('bb.me'))"))

    def test_an_unreachable_board_is_not_a_bad_sign_in(self):
        token = self.client.post("/api/agents", json={"handle": "jac", "kind": "human"}).json()["token"]
        page = self.page({"handle": "jac", "token": token}, down=("/api/me",))
        self.assertFalse(page.eval_on_selector("#signin", "d => d.open"))
        self.assertEqual(page.inner_text("#whoami"), "posting as @jac")


if __name__ == "__main__":
    unittest.main()
