"""deploy/forgejo-native: a pinned Forgejo without Docker (#651). The release is a fake file that answers
/api/v1/version on the port in app.ini; nothing is downloaded. Run with python -m unittest discover -s tests."""
import hashlib
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "forgejo-native"
FAKE = """#!/usr/bin/env python3
import http.server, re, sys
if "--version" in sys.argv:
    print("forgejo version 17.0.2"); sys.exit(0)
ini = open(sys.argv[sys.argv.index("--work-path") + 1] + "/custom/conf/app.ini").read()
port = int(re.search(r"^HTTP_PORT *= *(\\d+)", ini, re.M).group(1))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"version":"fake"}')
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
"""


@unittest.skipUnless(os.uname().sysname == "Linux" and os.getuid() != 0, "the script is for Linux, not root")
class ForgejoNative(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(os.uname().machine)
        if not arch:
            self.skipTest("no Forgejo for this machine")
        rel = t / "release"; rel.mkdir()
        self.release = rel / f"forgejo-17.0.2-linux-{arch}"
        self.release.write_text(FAKE)
        self.sha = hashlib.sha256(FAKE.encode()).hexdigest()
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.env = {**os.environ, "FORGEJO_DIR": str(t / "fj"), "FORGEJO_BASE_URL": rel.as_uri(),
                    "FORGEJO_SHA256": self.sha, "FORGEJO_PORT": str(self.port), "BOARD": "http://10.1.2.3:8080"}
        self.addCleanup(lambda: self.run_cmd("stop"))

    def run_cmd(self, *a, env=None):
        return subprocess.run([str(SCRIPT), *a], env=env or self.env, capture_output=True, text=True, timeout=60)

    def test_start_writes_settings_once_and_stop_stops(self):
        r = self.run_cmd("start"); self.assertEqual(r.returncode, 0, r.stderr)
        ini = Path(self.tmp.name, "fj/work/custom/conf/app.ini").read_text()
        self.assertIn(f"HTTP_PORT = {self.port}", ini)
        self.assertIn("ALLOWED_HOST_LIST = 10.1.2.3\n", ini)     # only the board, not every private address
        self.assertIn("INSTALL_LOCK = true", ini)
        self.assertIn(f"export FORGE=http://127.0.0.1:{self.port} ", self.run_cmd("env").stdout)
        self.assertIn("already running", self.run_cmd("start").stdout)
        self.assertEqual(self.run_cmd("status").returncode, 0)
        self.assertEqual(self.run_cmd("stop").returncode, 0)
        self.assertEqual(self.run_cmd("status").returncode, 1)

    def test_a_stale_pid_file_is_not_trusted(self):
        r = self.run_cmd("start"); self.assertEqual(r.returncode, 0, r.stderr)
        self.run_cmd("stop")
        other = subprocess.Popen(["sleep", "30"]); self.addCleanup(other.kill)
        Path(self.tmp.name, "fj/forgejo.pid").write_text(str(other.pid))
        self.assertEqual(self.run_cmd("stop").returncode, 0)
        self.assertIsNone(other.poll(), "stop must not kill a process that is not Forgejo")

    def test_start_replaces_a_stale_pid_file(self):
        other = subprocess.Popen(["sleep", "30"]); self.addCleanup(other.kill)
        Path(self.tmp.name, "fj").mkdir(); Path(self.tmp.name, "fj/forgejo.pid").write_text(str(other.pid))
        r = self.run_cmd("start"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("already running", r.stdout)
        self.assertIsNone(other.poll())

    def test_the_pin_is_overridden_only_with_both_variables(self):
        r = self.run_cmd("start", env={k: v for k, v in self.env.items() if k != "FORGEJO_BASE_URL"})
        self.assertNotEqual(r.returncode, 0); self.assertIn("go together", r.stderr)
        self.assertIn("NOT the pinned release", self.run_cmd("start").stdout)

    def test_a_taken_port_moves_to_the_next(self):
        s = socket.socket(); s.bind(("127.0.0.1", self.port)); s.listen()
        self.addCleanup(s.close)
        r = self.run_cmd("start"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"HTTP_PORT = {self.port + 1}", Path(self.tmp.name, "fj/work/custom/conf/app.ini").read_text())

    def test_a_download_that_does_not_match_the_pin_is_not_used(self):
        r = self.run_cmd("start", env={**self.env, "FORGEJO_SHA256": "0" * 64})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pinned checksum", r.stderr)
        self.assertFalse(Path(self.tmp.name, "fj/forgejo-17.0.2").exists())



@unittest.skipUnless(os.getuid() != 0, "the script is not for root")
class ForgejoNativeOnAMac(unittest.TestCase):
    """macOS (#651): the binary is Homebrew's, found on the PATH, and a process is found by `ps`. Faked on any machine:
    `uname` says Darwin, and the Forgejo on the PATH is the fake server."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.bin = t / "bin"; self.bin.mkdir()
        (self.bin / "uname").write_text('#!/bin/sh\nif [ "$1" = -m ]; then echo arm64; else echo Darwin; fi\n')
        (self.bin / "uname").chmod(0o755)
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "FORGEJO_DIR": str(t / "fj"),
                    "FORGEJO_PORT": str(self.port), "BOARD": "http://10.1.2.3:8080"}
        self.addCleanup(lambda: self.run_cmd("stop"))

    def run_cmd(self, *a):
        return subprocess.run([str(SCRIPT), *a], env=self.env, capture_output=True, text=True, timeout=60)

    def test_it_runs_the_forgejo_on_the_path_without_downloading_and_stops_it(self):
        (self.bin / "forgejo").write_text(FAKE); (self.bin / "forgejo").chmod(0o755)
        r = self.run_cmd("start"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("downloading", r.stdout)
        self.assertNotIn("the pin is", r.stdout, "17.0.2 is the pin")
        self.assertIn(f"FORGEJO_BIN={self.bin}/forgejo ", self.run_cmd("env").stdout)
        self.assertIn("already running", self.run_cmd("start").stdout)
        self.assertEqual(self.run_cmd("stop").returncode, 0)
        self.assertEqual(self.run_cmd("status").returncode, 1)

    def test_a_different_version_is_said_not_refused(self):
        (self.bin / "forgejo").write_text(FAKE.replace("17.0.2", "17.1.0")); (self.bin / "forgejo").chmod(0o755)
        r = self.run_cmd("start"); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("this Mac's Forgejo is 17.1.0, the pin is 17.0.2", r.stdout)

    def test_without_forgejo_or_homebrew_it_says_what_to_install(self):
        env = {**self.env, "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin"}
        r = subprocess.run([str(SCRIPT), "start"], env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("install Homebrew", r.stderr)

if __name__ == "__main__":
    unittest.main()
