"""The board page: a workspace folds completely, unread or not (#373), and a thread with no replies
can be replied to from the channel (#372). Needs Playwright and Chromium; skipped without them.
Run with python -m unittest discover -s tests."""
import json
import os
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


class BoardRail(unittest.TestCase):
    def setUp(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Install Playwright and Chromium for board page checks")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(db, "DB_PATH", os.path.join(tmp, "board.db")))
        self.stack.enter_context(patch.object(db, "_local", threading.local()))
        self.stack.enter_context(patch.object(main, "READ_TOKEN_REQUIRED", False))
        self.client = self.stack.enter_context(TestClient(main.app))
        self.addCleanup(lambda: hasattr(db._local, "conn") and db._local.conn.close())
        self.token = self.client.post("/api/agents", json={"handle": "jac", "kind": "human"}).json()["token"]
        self.human = {"Authorization": "Bearer " + self.token}
        for ws in ("aaa", "bbb"):
            self.client.post("/api/workspaces", headers=self.human, json={"slug": ws, "name": ws, "purpose": "x"}).raise_for_status()
            self.root = self.client.post("/api/messages", headers=self.human, json={"channel": ws, "body": "forgot something"}).json()["message"]["id"]
        pw = self.stack.enter_context(sync_playwright())
        browser = pw.chromium.launch()
        self.stack.callback(browser.close)
        self.page = browser.new_page()
        self.errors = []
        self.page.on("pageerror", lambda err: self.errors.append(str(err)))

        def serve(route):
            path = route.request.url.replace("http://board.test", "")
            if "wait=" in path:
                return route.abort()
            r = self.client.request(route.request.method, path, content=route.request.post_data, headers=route.request.headers)
            route.fulfill(status=r.status_code, body=r.content, content_type=r.headers.get("content-type", "text/plain"))
        self.page.route("**/*", serve)
        self.page.add_init_script("localStorage.setItem('bb.me', " + json.dumps(json.dumps({"handle": "jac", "token": self.token})) + ");"
                                  "localStorage.setItem('bb.seen', '{\"aaa\":0,\"bbb\":0}')")

    def rail(self):
        return self.page.eval_on_selector_all("#channels li.sub", "ls => ls.map(l => l.querySelector('.name').title)")

    def test_a_folded_workspace_shows_only_its_unread_count(self):
        self.page.goto("http://board.test/#bbb")
        self.page.wait_for_selector("#channels li.ws")
        self.assertEqual(self.rail(), ["#bbb"], "the open channel stays; unread in the other workspace does not")
        self.assertEqual(self.page.inner_text("#channels li.ws[data-proj=aaa] .badge.sum"), "1")
        self.page.click("#channels li.ws[data-proj=aaa] button.fold")
        self.assertEqual(sorted(self.rail()), ["#aaa", "#bbb"], "opened, it lists its channels")
        self.page.click("#channels li.ws[data-proj=aaa] button.fold")
        self.assertEqual(self.rail(), ["#bbb"], "and folds again, unread or not")
        self.assertEqual(self.errors, [])

    def test_a_thread_with_no_replies_can_be_replied_to(self):
        self.page.goto("http://board.test/#bbb")
        button = f"#m{self.root} button.replies.none"
        self.page.wait_for_selector(button, state="attached")
        self.page.hover(f"#m{self.root}")
        self.page.click(button)
        self.page.wait_for_selector(f"#t{self.root}")
        self.page.fill("#thread textarea", "and one more thing")
        self.page.click("#thread button.send")
        self.page.wait_for_function("id => document.querySelector('#m' + id + ' button.replies .n').textContent === '1 reply'", arg=self.root)
        self.assertEqual(self.client.get(f"/api/threads/{self.root}").json()["messages"][-1]["body"], "and one more thing")
        self.assertEqual(self.page.locator(f"#t{self.root} button.replies").count(), 0, "no button inside the thread itself")
        self.assertEqual(self.errors, [])

    def test_a_thread_stays_at_its_last_message_while_images_load(self):
        import base64
        import io
        from PIL import Image
        # a thread taller than the viewport, with a tall image as its last reply. The image has no
        # size until it loads, so scrolling to the bottom first, then loading it, used to slide the
        # view up to the middle (jac). The thread must stay on the last message.
        for i in range(8):
            self.client.post("/api/messages", headers=self.human,
                             json={"channel": "bbb", "reply_to": self.root, "body": "reply %d" % i}).raise_for_status()
        buf = io.BytesIO(); Image.new("RGB", (10, 400), "red").save(buf, "PNG")
        self.client.post("/api/messages", headers=self.human,
                         json={"channel": "bbb", "reply_to": self.root, "body": "the picture",
                               "image_attachment": base64.b64encode(buf.getvalue()).decode("ascii")}).raise_for_status()
        self.page.set_viewport_size({"width": 560, "height": 420})
        self.page.goto("http://board.test/#bbb")
        self.page.wait_for_selector("#m%d button.replies" % self.root, state="attached")
        self.page.hover("#m%d" % self.root)
        self.page.click("#m%d button.replies" % self.root)
        self.page.wait_for_selector("#t%d" % self.root)
        self.page.wait_for_function("() => { var i = document.querySelector('#tlist img.attached-image');"
                                    " return i && i.complete && i.naturalHeight > 0; }")
        gap = self.page.evaluate("() => { var p = document.querySelector('#tlist');"
                                 " return p.scrollHeight - p.scrollTop - p.clientHeight; }")
        self.assertLess(gap, 40, "the thread stays pinned to its last message after the image loads")
        self.assertEqual(self.errors, [])

    def test_a_thread_stays_at_its_last_message_when_a_message_grows(self):
        # a text thread taller than the viewport; the receipt and worker chips the refreshes rewrite
        # grow a message after the scroll to the bottom. The thread must follow, not slide up (jac, 8971).
        for i in range(12):
            self.client.post("/api/messages", headers=self.human,
                             json={"channel": "bbb", "reply_to": self.root, "body": "reply number %d" % i}).raise_for_status()
        self.page.set_viewport_size({"width": 560, "height": 360})
        self.page.goto("http://board.test/#bbb")
        self.page.wait_for_selector("#m%d button.replies" % self.root, state="attached")
        self.page.hover("#m%d" % self.root)
        self.page.click("#m%d button.replies" % self.root)
        self.page.wait_for_selector("#t%d" % self.root)
        # a chip rewrite that grows the last message, the way refreshReceipts/refreshWorkers do; this
        # grows the content below the viewport anchor, where the browser does not keep the bottom
        self.page.evaluate("() => { var ms = document.querySelectorAll('#tlist .msg'); var m = ms[ms.length - 1];"
                           " m.insertAdjacentHTML('beforeend', '<div style=\"height:300px\"></div>'); }")
        gap = self.page.evaluate("() => { var p = document.querySelector('#tlist');"
                                 " return p.scrollHeight - p.scrollTop - p.clientHeight; }")
        self.assertLess(gap, 40, "the thread stays pinned to its last message after a message grows")
        self.assertEqual(self.errors, [])

    def test_a_popped_out_thread_fills_a_narrow_window(self):
        # the popout (?solo) is 780px wide, under the 980px overlay breakpoint; its panel must still
        # start at the left edge, not sit as a 420px overlay beside a blank area (jac, 11806)
        self.page.set_viewport_size({"width": 780, "height": 600})
        self.page.goto("http://board.test/t/%d?solo" % self.root)
        self.page.wait_for_selector("#t%d" % self.root)
        box = self.page.eval_on_selector("aside.thread", "e => { var r = e.getBoundingClientRect(); return [r.left, r.width]; }")
        self.assertEqual([round(x) for x in box], [0, 780])
        self.assertEqual(self.errors, [])

    def test_hovering_a_channel_lists_its_bots_with_their_profiles(self):
        for handle in ("tarzan", "jane", "bound", "session"):
            self.client.post("/api/agents", json={"handle": handle, "kind": "claude-code", "workspace": "aaa"}).raise_for_status()
        self.client.put("/api/agents/session/interactive", headers=self.human, json={"interactive": True}).raise_for_status()
        with db.conn() as c:     # "bound" works only in another channel of aaa, so it is not listed in #aaa
            c.execute("INSERT INTO fleet_bots (handle, host, harness, run, set_by, set_at, channels) VALUES ('bound','box','claude',0,'jac','now','[\"aaa.dev\"]')")
        self.client.post("/api/agents", json={"handle": "outsider", "kind": "claude-code", "workspace": "bbb"}).raise_for_status()
        self.page.goto("http://board.test/#bbb")
        self.page.wait_for_selector("#channels li.ws")
        self.page.click("#channels li.ws[data-proj=aaa] .name")
        self.page.hover("#channels li[data-slug=aaa]")
        self.page.wait_for_selector(".crew:not([hidden])")
        self.assertEqual(self.page.inner_text(".crew h3"), "#aaa · 2 bots")
        self.assertEqual(self.page.eval_on_selector_all(".crew .who", "e => e.map(x => x.textContent)"), ["@jane", "@tarzan"])
        self.page.hover("#channels li[data-slug=bbb]")
        self.page.wait_for_function("document.querySelector('.crew h3').innerText.includes('bbb')")
        self.assertEqual(self.page.eval_on_selector_all(".crew .who", "e => e.map(x => x.textContent)"), ["@outsider"])
        # a workspace row lists every bot of the workspace, "bound" included, never the interactive session
        self.page.hover("#channels li.ws[data-proj=aaa]")
        self.page.wait_for_function("document.querySelector('.crew h3').innerText.startsWith('aaa')")
        self.assertEqual(self.page.inner_text(".crew h3"), "aaa · 3 bots")
        self.assertEqual(self.page.eval_on_selector_all(".crew .who", "e => e.map(x => x.textContent)"), ["@bound", "@jane", "@tarzan"])
        self.page.mouse.move(700, 300)
        self.page.wait_for_selector(".crew", state="hidden")
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
