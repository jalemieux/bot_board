"""The Light / Dark / Auto switch in the top bar (board thread 12268). The HTML checks run anywhere; the browser
check needs Playwright and Chromium and is skipped without them. Run with python -m unittest discover -s tests."""
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
from app import db, main, web                    # noqa: E402

PAGES = ("/", "/fleet", "/routines", "/workspaces", "/onboard", "/search?q=x")


class Setup(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(db, "DB_PATH", os.path.join(tmp, "board.db")))
        self.stack.enter_context(patch.object(db, "_local", threading.local()))
        self.stack.enter_context(patch.object(main, "READ_TOKEN_REQUIRED", False))
        self.client = self.stack.enter_context(TestClient(main.app))
        self.addCleanup(lambda: hasattr(db._local, "conn") and db._local.conn.close())


class ThemeHtml(Setup):
    def test_every_page_has_the_switch_the_early_script_and_the_script_that_runs_it(self):
        for path in PAGES:
            h = self.client.get(path).text
            self.assertEqual(h.count('id="themebtn"'), 1, path)
            self.assertIn("document.documentElement.setAttribute('data-theme',t)", h, path)
            self.assertIn("getElementById('themebtn')", h, path)
            self.assertLess(h.index("data-theme',t)"), h.index("<style>"), "the choice is applied before the styles paint")

    def test_the_os_dark_palette_and_the_forced_one_are_the_same_variables(self):
        for css in (web.CSS, web.APP_CSS):
            auto = re.search(r"@media \(prefers-color-scheme:dark\)\{:root:not\(\[data-theme=light\]\)\{([^}]*)\}\}", css)
            forced = re.search(r":root\[data-theme=dark\]\{color-scheme:dark;([^}]*)\}", css)
            self.assertTrue(auto and forced)
            self.assertEqual(auto.group(1), forced.group(1))
            self.assertIn("--bg:#14150f", auto.group(1))
            self.assertIn(":root[data-theme=light]{color-scheme:light}", css)

    def test_no_dark_rule_ignores_a_chosen_light_theme(self):
        # every prefers-color-scheme:dark block must leave out data-theme=light, or Light would not hold on a dark OS
        for path in PAGES:
            h = self.client.get(path).text
            for m in re.finditer(r"prefers-color-scheme:\s*dark\)\s*\{", h):
                self.assertTrue(h[m.end():].startswith(":root:not([data-theme=light])"), (path, h[m.start():m.end() + 60]))


class ThemeBrowser(Setup):
    def setUp(self):
        super().setUp()
        try:
            from playwright.sync_api import sync_playwright
            pw = self.stack.enter_context(sync_playwright())
            browser = pw.chromium.launch()
        except Exception as e:      # no Playwright, or no Chromium that can start
            self.skipTest(f"no browser: {str(e).splitlines()[0][:80]}")
        self.stack.callback(browser.close)
        self.browser = browser

    def page(self, scheme):
        ctx = self.browser.new_context(color_scheme=scheme)
        self.stack.callback(ctx.close)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))

        def serve(route):
            path = route.request.url.replace("http://board.test", "")
            if "wait=" in path:
                return route.abort()
            r = self.client.request(route.request.method, path, content=route.request.post_data, headers=route.request.headers)
            route.fulfill(status=r.status_code, body=r.content, content_type=r.headers.get("content-type", "text/plain"))
        page.route("**/*", serve)
        return page, errors

    def bg(self, page):
        return page.evaluate("getComputedStyle(document.body).backgroundColor")

    def test_the_switch_cycles_auto_light_dark_and_the_choice_survives_a_reload(self):
        dark, light = "rgb(20, 21, 15)", "rgb(250, 249, 247)"
        for path in ("/", "/fleet"):
            page, errors = self.page("dark")             # an operating system set to dark
            page.goto("http://board.test" + path)
            self.assertEqual(page.inner_text("#themebtn"), "theme: auto")
            self.assertEqual(self.bg(page), dark, "Auto follows the OS")
            page.click("#themebtn")
            self.assertEqual((page.inner_text("#themebtn"), self.bg(page)), ("theme: light", light), "Light wins over a dark OS")
            page.reload()
            self.assertEqual((page.inner_text("#themebtn"), self.bg(page)), ("theme: light", light), "and is kept")
            page.click("#themebtn")
            self.assertEqual((page.inner_text("#themebtn"), self.bg(page)), ("theme: dark", dark))
            page.click("#themebtn")
            self.assertEqual((page.inner_text("#themebtn"), self.bg(page)), ("theme: auto", dark))
            self.assertEqual(errors, [])
        page, _ = self.page("light")                    # an OS set to light, Dark chosen
        page.goto("http://board.test/")
        page.click("#themebtn"); page.click("#themebtn")
        self.assertEqual((page.inner_text("#themebtn"), self.bg(page)), ("theme: dark", dark))


if __name__ == "__main__":
    unittest.main()
