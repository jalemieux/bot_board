"""#715: the forge client is narrow. It creates or finds fleet/<slug> and does nothing else, sends its token only to
FORGEJO_URL, follows no redirect, is bounded in time and size, and refuses a crafted name. A local server stands in for
Forgejo. Run with python -m unittest discover -s tests."""
import json
import re
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import fleet, forge


class Forge:
    """A stand-in forge: repos maps owner/name to {"owner", "empty"}; seen records every request."""
    def __init__(self, repos=None):
        self.repos, self.seen, self.mode = dict(repos or {}), [], None
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, data=None, headers=None):
                raw = json.dumps(data).encode() if data is not None else b""
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except OSError:
                    pass        # the client gave up first (a size cap or a timeout)

            def do_GET(self):
                outer.seen.append(("GET", self.path, self.headers.get("Authorization")))
                if outer.mode == "redirect":
                    return self.reply(302, headers={"Location": outer.other + "/stolen"})
                if outer.mode == "big":
                    return self.reply(200, {"x": "a" * (forge.MAX_BYTES + 10)})
                if outer.mode == "slow":
                    time.sleep(1.0)
                m = re.fullmatch(r"/api/v1/repos/([^/]+)/([^/]+)", self.path)
                r = outer.repos.get(m.group(1) + "/" + m.group(2)) if m else None
                if r is None:
                    return self.reply(404, {"message": "not found"})
                self.reply(200, {"full_name": m.group(1) + "/" + m.group(2), "owner": {"login": r["owner"]}, "empty": r["empty"]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                outer.seen.append(("POST", self.path, self.headers.get("Authorization"), body))
                if self.path != "/api/v1/user/repos":
                    return self.reply(404)
                outer.repos["fleet/" + body["name"]] = {"owner": "fleet", "empty": True}
                self.reply(201, {"full_name": "fleet/" + body["name"]})

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class ForgeClient(unittest.TestCase):
    def setUp(self):
        self.f = Forge()
        self.addCleanup(self.f.stop)
        self.set(self.f.url)

    def set(self, url, token="tok", read=""):
        for p in (mock.patch.object(fleet, "forge_url", lambda: url),
                  mock.patch.object(fleet, "forge_token", lambda read_=False, **kw: (read or token) if kw.get("read") else token)):
            p.start()
            self.addCleanup(p.stop)

    def test_it_creates_a_missing_repository_privately_and_empty(self):
        self.assertEqual(forge.make_repository("proj"), "fleet/proj")
        post = [s for s in self.f.seen if s[0] == "POST"][0]
        self.assertEqual(post[1:3], ("/api/v1/user/repos", "token tok"))
        self.assertEqual(post[3], {"name": "proj", "private": True, "auto_init": False})

    def test_it_adopts_an_empty_repository_of_fleet_and_refuses_the_rest(self):
        self.f.repos["fleet/a"] = {"owner": "fleet", "empty": True}
        self.f.repos["fleet/b"] = {"owner": "fleet", "empty": False}
        self.f.repos["fleet/c"] = {"owner": "someone", "empty": True}
        self.assertEqual(forge.make_repository("a"), "fleet/a")
        self.assertFalse([s for s in self.f.seen if s[0] == "POST"], "adopting creates nothing")
        with self.assertRaisesRegex(forge.ForgeError, "has content"):
            forge.make_repository("b")
        with self.assertRaisesRegex(forge.ForgeError, "belongs to someone"):
            forge.make_repository("c")

    def test_no_address_or_no_token_says_so(self):
        with mock.patch.object(fleet, "forge_url", lambda: ""), self.assertRaisesRegex(forge.ForgeError, "FORGEJO_URL"):
            forge.make_repository("x")
        with mock.patch.object(fleet, "forge_token", lambda **kw: ""), self.assertRaisesRegex(forge.ForgeError, "FORGEJO_TOKEN"):
            forge.make_repository("x")
        for bad in ("ftp://forge", "http://user:pw@forge.example", "http://forge.example/?x=1", "forge.example"):
            with mock.patch.object(fleet, "forge_url", lambda bad=bad: bad), self.assertRaises(forge.ForgeError, msg=bad):
                forge.make_repository("x")

    def test_a_crafted_name_never_reaches_the_forge(self):
        for bad in ("", ".", "..", "a/b", "a b", "../../admin", "-x", "x.git", "a" * 101, "x?y=1", "x#", "x%2e", None):
            with self.assertRaises(forge.ForgeError, msg=repr(bad)):
                forge.make_repository(bad)
        with self.assertRaises(forge.ForgeError):
            forge.get_repository("..", "x")
        self.assertEqual(self.f.seen, [])

    def test_no_redirect_is_followed_with_the_token(self):
        other = Forge()
        self.addCleanup(other.stop)
        self.f.mode, self.f.other = "redirect", other.url
        with self.assertRaisesRegex(forge.ForgeError, "redirect"):
            forge.get_repository("fleet", "x")
        self.assertEqual(other.seen, [], "the token must not follow a redirect")

    def test_a_huge_answer_and_a_slow_one_are_refused(self):
        self.f.mode = "big"
        with self.assertRaisesRegex(forge.ForgeError, "larger"):
            forge.get_repository("fleet", "x")
        self.f.mode = "slow"
        with mock.patch.object(forge, "TIMEOUT", 0.2), self.assertRaisesRegex(forge.ForgeError, "did not answer"):
            forge.get_repository("fleet", "x")

    def test_a_dead_forge_is_an_error_not_a_crash(self):
        self.f.stop()
        with self.assertRaisesRegex(forge.ForgeError, "did not answer"):
            forge.get_repository("fleet", "x")

    def test_the_read_token_is_used_for_reads_and_the_write_token_for_creating(self):
        self.set(self.f.url, token="write", read="read")
        forge.get_repository("fleet", "x", read=True)
        self.assertEqual(self.f.seen[-1][2], "token read")
        forge.make_repository("y")
        self.assertTrue(all(s[2] == "token write" for s in self.f.seen[-2:]))

    def test_an_environment_proxy_never_sees_the_token(self):
        import importlib
        import os
        import urllib.request
        self.addCleanup(importlib.reload, forge)       # the opener is built when the module loads: build it again with a proxy set
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.invalid:3128", "http_proxy": "http://proxy.invalid:3128",
                                          "HTTPS_PROXY": "http://proxy.invalid:3128", "https_proxy": "http://proxy.invalid:3128"}):
            importlib.reload(forge)
            self.assertEqual([h for h in forge._opener.handlers if isinstance(h, urllib.request.ProxyHandler)], [],
                             "the opener has no proxy handler, whatever the environment says")
            plain = urllib.request.build_opener(forge._NoRedirect)
            self.assertTrue([h for h in plain.handlers if isinstance(h, urllib.request.ProxyHandler)], "the default opener would use it")

    def test_two_callers_at_once_create_it_once(self):
        from app import workspaces
        ws = {"slug": "g", "archived": 0, "repository": None}
        made = []

        def slow_make(slug, timeout=None):
            time.sleep(0.2)
            made.append(slug)
            return "fleet/" + slug

        def link(slug, full, agent):
            ws["repository"] = full
        with mock.patch.object(workspaces, "find", lambda s: ws), mock.patch.object(workspaces, "can_manage", lambda w, a: True), \
                mock.patch.object(workspaces, "set_repository", link), mock.patch.object(forge, "make_repository", slow_make):
            out = []
            ts = [threading.Thread(target=lambda: out.append(forge.ensure_repository("g", {}))) for _ in range(3)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        self.assertEqual(made, ["g"], "the forge is asked once")
        self.assertEqual([o["repository"] for o in out], ["fleet/g"] * 3)

    def test_only_the_forge_client_asks_for_the_token_value(self):
        callers = []
        for path in (ROOT / "app").glob("*.py"):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if "forge_token(" in line and not line.lstrip().startswith(("#", "def ", '"""')) and "def forge_token" not in line:
                    callers.append(path.name)
        self.assertEqual(set(callers), {"forge.py"}, callers)


if __name__ == "__main__":
    unittest.main()
