"""The roster says which machine each bot is on, and the channel hover card shows it (board thread 12365).
Run with python -m unittest discover -s tests."""
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


class Machine(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(db, "DB_PATH", os.path.join(tmp, "board.db")))
        self.stack.enter_context(patch.object(db, "_local", threading.local()))
        self.stack.enter_context(patch.object(main, "READ_TOKEN_REQUIRED", False))
        self.client = self.stack.enter_context(TestClient(main.app))
        self.addCleanup(lambda: hasattr(db._local, "conn") and db._local.conn.close())
        self.human = {"Authorization": "Bearer " + self.client.post("/api/agents", json={"handle": "jac", "kind": "human"}).json()["token"]}
        self.client.post("/api/workspaces", headers=self.human, json={"slug": "aaa", "name": "aaa", "purpose": "x"})
        for h in ("ann", "bea", "cy"):
            self.client.post("/api/agents", json={"handle": h, "kind": "claude-code", "workspace": "aaa"}).raise_for_status()

    def report(self, box, *bots):
        r = self.client.post("/api/agents", json={"handle": f"keeper-{box}", "kind": "keeper"})
        token = r.json().get("token")
        self.tokens = getattr(self, "tokens", {})
        self.tokens.setdefault(box, token)
        self.client.put(f"/api/fleet/hosts/{box}", headers={"Authorization": "Bearer " + self.tokens[box]},
                        json={"bots": [{"handle": h, "harness": "claude", "status": st, "serves": "/pool", "pool": True}
                                       for h, st in bots]}).raise_for_status()

    def activity(self, handle):
        return {a["handle"]: a["activity"] for a in self.client.get("/api/agents").json()["agents"]}[handle]

    def report_health(self, box, handle, health):
        self.client.put(f"/api/fleet/hosts/{box}", headers={"Authorization": "Bearer " + self.tokens[box]},
                        json={"bots": [{"handle": handle, "harness": "claude", "status": "waiting", "serves": "/pool",
                                        "pool": True, "health": health}]}).raise_for_status()

    def machines(self):
        return {a["handle"]: a["machine"] for a in self.client.get("/api/agents").json()["agents"]}

    def test_a_bot_is_on_the_box_that_reports_it_and_one_no_keeper_reports_has_none(self):
        self.report("minipc-1", ("ann", "waiting"))
        self.report("minipc-2", ("bea", "stopped"))
        m = self.machines()
        self.assertEqual((m["ann"], m["bea"], m["cy"], m["jac"]), ("minipc-1", "minipc-2", None, None))

    def test_a_bot_on_two_boxes_is_on_the_one_where_it_is_up(self):
        self.report("box-a", ("ann", "stopped"))
        self.report("box-b", ("ann", "waiting"))
        self.assertEqual(self.machines()["ann"], "box-b")
        self.report("box-a", ("ann", "running aaa"))            # up on both: the first
        self.assertEqual(self.machines()["ann"], "box-a")

    def test_a_bot_is_idle_working_or_stopped_while_its_keeper_reports_and_unknown_after(self):
        self.report("box-a", ("ann", "waiting"), ("bea", "running aaa"), ("cy", "stopped"))
        agents = {a["handle"]: a for a in self.client.get("/api/agents").json()["agents"]}
        self.assertEqual([agents[h]["activity"] for h in ("ann", "bea", "cy", "jac")], ["idle", "working", "stopped", None])
        self.assertEqual(self.activity("ann"), "idle")
        self.report_health("box-a", "ann", "logged out of claude: run claude login on the box")
        self.assertEqual(self.activity("ann"), "needs login", "a bot that cannot work is not idle")
        db.conn().execute("UPDATE fleet_hosts SET reported_at='2020-01-01T00:00:00+00:00'")
        db.conn().commit()
        agents = {a["handle"]: a for a in self.client.get("/api/agents").json()["agents"]}
        self.assertEqual((agents["ann"]["machine"], agents["ann"]["activity"]), ("box-a", None), "a silent keeper's bots are on a box but of unknown activity")

    def test_the_page_carries_it_too(self):
        self.report("minipc-1", ("ann", "waiting"))
        html = self.client.get("/").text
        self.assertIn('"machine": "minipc-1"', html)


class MachineInTheCard(unittest.TestCase):
    """The card line, in a browser; skipped without Playwright and Chromium."""

    def test_the_hover_card_names_the_machine(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Install Playwright and Chromium for board page checks")
        m = Machine("test_a_bot_is_on_the_box_that_reports_it_and_one_no_keeper_reports_has_none")
        m.setUp()
        self.addCleanup(m.stack.close)
        m.report("minipc-1", ("ann", "waiting"))
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as e:
                self.skipTest(f"no browser: {str(e).splitlines()[0][:80]}")
            try:
                page = browser.new_page()
                page.add_init_script("localStorage.setItem('bb.me', JSON.stringify({handle:'jac', token:'%s'}))"
                                     % m.human["Authorization"].split()[1])

                def serve(route):
                    path = route.request.url.replace("http://board.test", "")
                    if "wait=" in path:
                        return route.abort()
                    r = m.client.request(route.request.method, path, content=route.request.post_data, headers=route.request.headers)
                    route.fulfill(status=r.status_code, body=r.content, content_type=r.headers.get("content-type", "text/plain"))
                page.route("**/*", serve)
                page.goto("http://board.test/#aaa")
                page.wait_for_selector("#channels li.ws")
                page.click("#channels li.ws[data-proj=aaa] .name")      # unfold the workspace, then hover its channel
                page.hover("#channels li[data-slug=aaa]")
                page.wait_for_selector(".crew:not([hidden])")
                rows = page.eval_on_selector_all(".crew .bot", "e => e.map(x => x.innerText.replace(/\\s+/g, ' ').trim())")
                ann = next(r for r in rows if r.startswith("@ann"))
                bea = next(r for r in rows if r.startswith("@bea"))
                self.assertIn("on minipc-1 · idle", ann)
                self.assertNotIn(" on ", bea, "a bot no keeper reports shows no machine line")
            finally:
                browser.close()

    def test_hovering_an_at_mention_or_an_author_shows_that_agent_in_the_card(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Install Playwright and Chromium for board page checks")
        m = Machine("test_a_bot_is_on_the_box_that_reports_it_and_one_no_keeper_reports_has_none")
        m.setUp()
        self.addCleanup(m.stack.close)
        m.report("minipc-1", ("ann", "waiting"))
        m.client.post("/api/messages", headers=m.human, json={"channel": "aaa", "body": "over to @ann, not @nobody, and cc @jac"}).raise_for_status()
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as e:
                self.skipTest(f"no browser: {str(e).splitlines()[0][:80]}")
            try:
                page = browser.new_page(viewport={"width": 1100, "height": 600})
                page.add_init_script("localStorage.setItem('bb.me', JSON.stringify({handle:'jac', token:'%s'}))"
                                     % m.human["Authorization"].split()[1])

                def serve(route):
                    path = route.request.url.replace("http://board.test", "")
                    if "wait=" in path:
                        return route.abort()
                    r = m.client.request(route.request.method, path, content=route.request.post_data, headers=route.request.headers)
                    route.fulfill(status=r.status_code, body=r.content, content_type=r.headers.get("content-type", "text/plain"))
                page.route("**/*", serve)
                page.goto("http://board.test/c/aaa")
                page.wait_for_selector("#stream .msg .body a.m")
                card = lambda: page.inner_text(".crew").replace("\n", " ")
                page.hover("#stream .msg .body a.m[href='/a/ann']")
                page.wait_for_selector(".crew:not([hidden])")
                self.assertIn("@ann", card())
                self.assertIn("on minipc-1 · idle", card())
                self.assertEqual(page.locator(".crew .bot").count(), 1, "one agent, not a list")
                page.mouse.move(900, 500)
                page.wait_for_selector(".crew", state="hidden")
                page.hover("#stream .msg .body a.m[href='/a/jac']")
                page.wait_for_selector(".crew:not([hidden])")
                self.assertIn("human", card(), "a person shows as human")
                page.mouse.move(900, 500)
                page.wait_for_selector(".crew", state="hidden")
                page.hover("#stream .msg .body a.m[href='/a/nobody']")
                page.wait_for_timeout(700)
                self.assertTrue(page.is_hidden(".crew"), "a name that is no agent gets no card")
                page.hover("#stream .msg .head a.who")
                page.wait_for_selector(".crew:not([hidden])")
                self.assertIn("@jac", card(), "the author's name shows the card too")
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
