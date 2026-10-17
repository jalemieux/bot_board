"""Human-started conversations and duplicate-safe initial requests."""
import concurrent.futures
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main


class ConversationStart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(db, "DB_PATH", os.path.join(self.tmp.name, "board.db")))
        stack.enter_context(patch.object(db, "_local", threading.local()))
        self.client = stack.enter_context(TestClient(main.app))
        self.human = self.register("operator", "human")
        self.worker = self.register("worker", "copilot")
        self.other = self.register("other", "copilot")

    def register(self, handle, kind):
        response = self.client.post("/api/agents", json={"handle": handle, "kind": kind})
        self.assertEqual(response.status_code, 201, response.text)
        return {"Authorization": "Bearer " + response.json()["token"]}

    def conversation(self):
        response = self.client.post("/api/conversations", headers=self.human,
                                    json={"participants": ["worker"]})
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()["conversation"]["slug"]

    def inbox(self, headers):
        return self.client.get("/api/inbox", headers=headers).json()["messages"]

    def test_first_request_replays_once_and_worker_receives_plain_replies(self):
        slug = self.conversation()
        self.assertEqual(self.inbox(self.worker), [])
        self.assertEqual(self.conversation(), slug)
        payload = {"channel": slug, "body": "@worker please inspect this", "request_id": "a" * 32}
        first = self.client.post("/api/messages", headers=self.human, json=payload)
        repeated = self.client.post("/api/messages", headers=self.human, json=payload)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(repeated.status_code, 201, repeated.text)
        mid = first.json()["message"]["id"]
        self.assertEqual(repeated.json()["message"]["id"], mid)
        self.assertEqual([m["id"] for m in self.inbox(self.worker)], [mid])
        self.assertEqual(self.inbox(self.other), [])
        self.assertEqual(self.client.post(f"/api/threads/{mid}/worker", headers=self.worker).status_code, 200)
        reply = self.client.post("/api/messages", headers=self.human, json={
            "channel": slug, "body": "More context", "reply_to": mid,
        })
        self.assertEqual(reply.status_code, 201, reply.text)
        self.assertEqual(reply.json()["message"]["thread_id"], mid)
        self.assertEqual([m["id"] for m in self.inbox(self.worker)], [mid, reply.json()["message"]["id"]])
        self.assertEqual(self.inbox(self.other), [])

    def test_concurrent_retries_and_conflicting_content(self):
        payload = {"channel": self.conversation(), "body": "@worker one request", "request_id": "b" * 32}
        refused = self.client.post("/api/messages", headers=self.human,
                                   json={**payload, "meta": {"kind": "context_update"}})
        self.assertEqual(refused.status_code, 403, refused.text)
        def send(_):
            response = self.client.post("/api/messages", headers=self.human, json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            return response.json()["message"]["id"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(send, range(4)))
        self.assertEqual(len(set(ids)), 1)
        for change in ({"body": "@worker changed"}, {"meta": {"changed": True}}, {"tags": ["changed"]}):
            response = self.client.post("/api/messages", headers=self.human, json={**payload, **change})
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.inbox(self.worker)), 1)
        for bad in ("short", "x" * 81, "invalid request id"):
            response = self.client.post("/api/messages", headers=self.human, json={**payload, "request_id": bad})
            self.assertEqual(response.status_code, 422, response.text)

    def test_retry_keys_are_author_scoped_and_ordinary_posts_stay_unchanged(self):
        payload = {"channel": "lobby", "body": "Same content", "request_id": "c" * 32}
        ids = []
        for auth in (self.human, self.worker):
            response = self.client.post("/api/messages", headers=auth, json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            ids.append(response.json()["message"]["id"])
        del payload["request_id"]
        for _ in range(2):
            response = self.client.post("/api/messages", headers=self.human, json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            ids.append(response.json()["message"]["id"])
        self.assertEqual(len(set(ids)), 4)
        with db.conn() as c:
            c.execute("DELETE FROM messages WHERE id=?", (ids[0],))
        self.assertEqual(db.conn().execute("SELECT COUNT(*) FROM message_requests").fetchone()[0], 1)

    def browser_flow(self, text, failure=None, signed_in=True):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Install playwright and Chromium for conversation UI checks")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                errors, posts = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                fail = [failure]

                def serve(route):
                    request = route.request
                    path = request.url.replace("http://board.test", "")
                    endpoint = urlsplit(path).path
                    if endpoint == "/api/messages" and "wait=30" in path:
                        route.abort()
                        return
                    if request.method == "POST" and endpoint == "/api/conversations" and fail[0] == "creation":
                        fail[0] = None
                        route.fulfill(status=503, json={"detail": "conversation unavailable"})
                        return
                    if request.method == "POST" and endpoint == "/api/messages":
                        posts.append(json.loads(request.post_data))
                        if fail[0] == "posting":
                            fail[0] = None
                            route.fulfill(status=503, json={"detail": "posting unavailable"})
                            return
                    response = self.client.request(request.method, path, content=request.post_data,
                                                   headers=request.headers)
                    if request.method == "POST" and endpoint == "/api/messages" and fail[0] == "lost_response":
                        fail[0] = None
                        route.abort()
                        return
                    route.fulfill(status=response.status_code, body=response.content,
                                  content_type=response.headers.get("content-type", "text/plain"))

                page.route("**/*", serve)
                if signed_in:
                    identity = {"handle": "operator", "token": self.human["Authorization"].removeprefix("Bearer ")}
                    page.add_init_script("localStorage.setItem('bb.me', " + json.dumps(json.dumps(identity)) + ")")
                page.goto("http://board.test/a/worker")
                page.get_by_role("link", name="Talk to @worker", exact=True).click()
                dialog = page.get_by_role("dialog", name="Talk to @worker")
                dialog.wait_for()
                self.assertIn("not private", dialog.inner_text())
                self.assertIn("root/fleet session", dialog.inner_text())
                before = self.client.get("/api/stats").json()["messages"]
                ta = dialog.get_by_role("textbox", name="Initial request")
                ta.fill(text)
                dialog.get_by_role("button", name="Send request", exact=True).click()
                if not signed_in:
                    signin = page.locator("#signin")
                    signin.get_by_label("handle", exact=True).fill("operator")
                    signin.get_by_role("button", name="sign in", exact=True).click()
                    page.wait_for_function("document.querySelector('#si-err').textContent.includes('is taken')")
                    self.assertEqual(ta.input_value(), text)
                    self.assertEqual(self.client.get("/api/stats").json()["messages"], before)
                    signin.get_by_label("token (optional)", exact=True).fill(
                        self.human["Authorization"].removeprefix("Bearer "))
                    signin.get_by_role("button", name="sign in", exact=True).click()
                    signin.wait_for(state="hidden")
                    dialog.get_by_role("button", name="Send request", exact=True).click()
                if failure:
                    page.wait_for_function("document.querySelector('#cv-err').textContent.length > 0")
                    self.assertEqual(ta.input_value(), text)
                    if failure == "lost_response":
                        page.reload()
                        dialog.wait_for()
                        self.assertEqual(ta.input_value(), text)
                    dialog.get_by_role("button", name="Retry request", exact=True).click()
                page.wait_for_url(re.compile(r"/t/\d+$"))
                mid = int(page.url.rsplit("/", 1)[1])
                root = self.client.get(f"/api/messages/{mid}").json()["message"]
                self.assertEqual(root["body"], "@worker " + text)
                self.assertEqual(self.client.get("/api/stats").json()["messages"], before + 1)
                if len(posts) > 1:
                    self.assertEqual(len({p["request_id"] for p in posts}), 1)
                self.assertEqual(self.client.post(f"/api/threads/{mid}/worker", headers=self.worker).status_code, 200)
                page.locator("#tcompose textarea").fill("Unmentioned follow-up")
                with page.expect_response(lambda r: r.request.method == "POST" and
                                          urlsplit(r.url).path == "/api/messages"):
                    page.locator("#tcompose .send").click()
                replies = self.client.get(f"/api/threads/{mid}").json()["messages"]
                self.assertEqual(replies[-1]["reply_to"], mid)
                self.assertEqual(replies[-1]["body"], "Unmentioned follow-up")
                self.assertEqual(self.inbox(self.other), [])
                self.assertFalse(errors, errors)
                return root["channel"]
            finally:
                browser.close()

    def test_profile_flow_creates_then_reuses_the_conversation(self):
        first = self.browser_flow("First task")
        second = self.browser_flow("Another task")
        self.assertEqual(first, second)
        self.assertEqual(len(self.client.get("/api/conversations?all=true").json()["conversations"]), 1)
        self.assertNotIn('href="/?talk=', self.client.get("/a/operator").text)

    def test_failures_retain_draft_and_lost_response_replays_after_reload(self):
        for failure in ("creation", "posting", "lost_response"):
            with self.subTest(failure=failure):
                self.browser_flow("Recover " + failure, failure=failure)

    def test_existing_signin_errors_preserve_the_request(self):
        self.browser_flow("Sign in then send", signed_in=False)


if __name__ == "__main__":
    unittest.main()
