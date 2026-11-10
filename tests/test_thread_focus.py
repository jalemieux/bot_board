"""Clicking a Mentions, Waiting or Unread item opens its thread at that message (board thread 12865), not at the
newest reply. The HTML checks run anywhere; the browser check needs Playwright and Chromium and is skipped without
them. Run with python -m unittest discover -s tests."""
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient       # noqa: E402
from app import db, main                         # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(db, "DB_PATH", os.path.join(tmp, "board.db")))
        self.stack.enter_context(patch.object(db, "_local", threading.local()))
        self.stack.enter_context(patch.object(main, "READ_TOKEN_REQUIRED", False))
        self.client = self.stack.enter_context(TestClient(main.app))
        self.addCleanup(lambda: hasattr(db._local, "conn") and db._local.conn.close())
        self.token = self.client.post("/api/agents", json={"handle": "jac", "kind": "human"}).json()["token"]
        self.jac = {"Authorization": "Bearer " + self.token}
        r = self.client.post("/api/agents", json={"handle": "tarzan", "kind": "claude-code", "workspace": "admin"})
        self.bot = {"Authorization": "Bearer " + r.json()["token"]}

    def post(self, who, body, **more):
        r = self.client.post("/api/messages", headers=who, json={"channel": "admin", "body": body, **more})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["message"]["id"]


class FocusHtml(Base):
    def test_the_page_knows_how_to_open_a_thread_at_a_message(self):
        page = self.client.get("/").text
        self.assertIn("function focusInThread(msgId)", page)
        self.assertIn("openThread(+r.dataset.thread, true, r.dataset.msg ? +r.dataset.msg : 0)", page)
        self.assertIn("async function openThread(id, push, focusMsg)", page)
        self.assertIn(".msg.flash{animation:bbflash", page)

    def test_every_item_button_that_opens_a_thread_names_its_message(self):
        page = self.client.get("/").text
        for needle in ("""'<button class="ctx" data-thread="' + tid + '" data-msg="' + m.id + '">""",
                       """'<button class="ctx" data-thread="' + m.thread_id + '" data-msg="' + m.id + '">""",
                       """'<button class="ctx" data-thread="' + m.id + '" data-msg="' + m.id + '">"""):
            self.assertIn(needle, page)


class FocusBrowser(Base):
    def setUp(self):
        super().setUp()
        try:
            from playwright.sync_api import sync_playwright
            pw = self.stack.enter_context(sync_playwright())
            browser = pw.chromium.launch()
        except Exception as e:
            self.skipTest(f"no browser: {str(e).splitlines()[0][:80]}")
        self.stack.callback(browser.close)
        self.page = browser.new_page(viewport={"width": 1300, "height": 700})
        self.errors = []
        self.page.on("pageerror", lambda err: self.errors.append(str(err)))

        def serve(route):
            path = route.request.url.replace("http://board.test", "")
            if "wait=" in path:
                return route.abort()
            r = self.client.request(route.request.method, path, content=route.request.post_data, headers=route.request.headers)
            route.fulfill(status=r.status_code, body=r.content, content_type=r.headers.get("content-type", "text/plain"))
        self.page.route("**/*", serve)
        self.page.add_init_script("localStorage.setItem('bb.me', " + json.dumps(json.dumps({"handle": "jac", "token": self.token})) + ")")

    def visible_in_panel(self, msg_id):
        return self.page.evaluate("""(id) => { var e = document.getElementById('t' + id), p = document.getElementById('plist');
            if (!e || !p) return null; var a = e.getBoundingClientRect(), b = p.getBoundingClientRect();
            return a.top >= b.top - 1 && a.bottom <= b.bottom + 1 }""", msg_id)

    def test_a_mention_in_the_middle_of_a_long_thread_opens_at_that_message_and_flashes(self):
        root = self.post(self.bot, "a long thread")
        for i in range(15):
            self.post(self.bot, f"filler {i} " + "words " * 30, reply_to=root)
        target = self.post(self.bot, "@jac the message that names you", reply_to=root)
        for i in range(30):
            self.post(self.bot, f"later {i} " + "words " * 30, reply_to=root)
        self.page.goto("http://board.test/mentions")
        self.page.wait_for_selector("#stream .mrow")
        self.page.click(f'#stream .mrow button.ctx[data-msg="{target}"]')
        self.page.wait_for_selector(f"#t{target}")
        self.assertTrue(self.visible_in_panel(target), "the mention is on screen, not scrolled past")
        self.assertIn("flash", self.page.get_attribute(f"#t{target}", "class"))
        self.assertFalse(self.visible_in_panel(root), "and the panel did not open at the top either")
        self.assertEqual(self.errors, [])

    def test_a_thread_opened_without_a_message_still_opens_at_the_newest_reply(self):
        root = self.post(self.bot, "a thread")
        for i in range(30):
            last = self.post(self.bot, f"reply {i} " + "words " * 30, reply_to=root)
        self.page.goto(f"http://board.test/t/{root}")
        self.page.wait_for_selector(f"#t{last}")
        self.assertTrue(self.visible_in_panel(last))


if __name__ == "__main__":
    unittest.main()
