"""#551: run.sh keeps the port and address the board was started with, so status, stop and update find it without PORT in the
environment, and status says in a line what it found. The board is a fake uvicorn in a scratch venv; nothing is installed.
Run with python -m unittest discover -s tests."""
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE_UVICORN = """#!/usr/bin/env python3
import http.server, json, os, sys
port = int(sys.argv[sys.argv.index("--port") + 1])
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "version": "dev", "commit": os.environ.get("FAKE_COMMIT", "abcdef0123456789abcdef"), "agents": 0}, ensure_ascii=False).encode())
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
"""


@unittest.skipUnless(os.uname().sysname in ("Linux", "Darwin"), "run.sh is for Linux and macOS")
class RunShPort(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.dir = t / "board"
        venv = self.dir / "venv" / "bin"; venv.mkdir(parents=True)
        (venv / "python").symlink_to(subprocess.run(["which", "python3"], capture_output=True, text=True).stdout.strip())
        (venv / "pip").write_text("#!/bin/sh\nexit 0\n"); (venv / "pip").chmod(0o755)
        (venv / "uvicorn").write_text(FAKE_UVICORN); (venv / "uvicorn").chmod(0o755)
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.env = {k: v for k, v in os.environ.items() if k not in ("PORT", "BIND")}
        self.env.update({"HOME": str(t), "BOT_BOARD_DIR": str(self.dir)})
        self.addCleanup(lambda: self.run_sh("stop"))

    def run_sh(self, *a, **env):
        return subprocess.run([str(ROOT / "run.sh"), *a], env={**self.env, **env}, capture_output=True, text=True, timeout=60)

    def test_status_and_stop_find_a_board_started_on_another_port_without_PORT_set(self):
        r = self.run_sh("up", PORT=str(self.port)); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.dir / "board.addr").read_text(), f"PORT={self.port}\nBIND=127.0.0.1\n")
        r = self.run_sh("status")        # no PORT in this run
        self.assertEqual((r.returncode, r.stdout), (0, f"running on http://127.0.0.1:{self.port}, commit abcdef012345\n"))
        self.assertNotIn("{", r.stdout, "a line a person reads, not the board's JSON")
        self.assertEqual(self.run_sh("stop").stdout.strip(), "stopped")
        r = self.run_sh("status")
        self.assertEqual((r.returncode, r.stdout), (1, f"not running on http://127.0.0.1:{self.port}\n"), "the stored port is still the one it names")

    def test_a_port_named_in_this_run_wins_and_is_kept_by_the_next_start(self):
        a = self.port
        s = socket.socket(); s.bind(("127.0.0.1", 0)); b = s.getsockname()[1]; s.close()
        self.assertEqual(self.run_sh("up", PORT=str(a)).returncode, 0)
        self.assertEqual(self.run_sh("up", PORT=str(b)).returncode, 0)
        self.assertEqual((self.dir / "board.addr").read_text(), f"PORT={b}\nBIND=127.0.0.1\n")
        self.assertIn(f":{b},", self.run_sh("status").stdout)
        self.assertEqual(self.run_sh("status", PORT=str(a)).returncode, 1, "a PORT in the environment is the one asked about")

    def test_a_board_bound_beyond_this_machine_says_so_and_the_commit_text_is_cleaned(self):
        r = self.run_sh("up", PORT=str(self.port), BIND="0.0.0.0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Bound to 0.0.0.0: anyone who can reach this machine", r.stdout)
        self.assertIn("BIND=127.0.0.1", r.stdout)
        r = self.run_sh("status")            # the stored BIND: still said, though the address shows 127.0.0.1
        self.assertIn(f"running on http://127.0.0.1:{self.port}", r.stdout)
        self.assertIn("Bound to 0.0.0.0", r.stdout)
        self.run_sh("stop")
        self.run_sh("up", PORT=str(self.port), BIND="127.0.0.1", FAKE_COMMIT="ab;rm -rf \x1b[31mcd")      # the stored BIND stays until a run names another
        out = self.run_sh("status").stdout
        self.assertNotIn("Bound to", out, "loopback says nothing")
        self.assertIn("commit abrm-rf", out)       # the ; and the space are gone
        self.assertNotIn("\x1b", out, "only letters, digits, dot, dash and underscore of the commit are printed")

    def test_a_board_that_was_never_started_is_asked_about_on_the_default_port(self):
        r = self.run_sh("status")
        self.assertEqual((r.returncode, r.stdout), (1, "not running on http://127.0.0.1:8080\n"))


if __name__ == "__main__":
    unittest.main()
