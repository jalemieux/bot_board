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
if os.environ.get("FAKE_FAIL"):
    sys.exit(1)
port = int(sys.argv[sys.argv.index("--port") + 1])
if os.environ.get("FAKE_ENV_OUT"):
    open(os.environ["FAKE_ENV_OUT"], "w").write(json.dumps({k: os.environ.get(k) for k in ("BOT_BOARD_ROUTINES", "BOARD", "ROUTINES_CONFIG_DIR")}))
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

    def board_env(self, **env):
        out = Path(self.tmp.name) / "board-env.json"
        r = self.run_sh("up", FAKE_ENV_OUT=str(out), **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        import json
        return json.loads(out.read_text())

    def test_the_board_runs_the_routines_runner_inside_itself_with_its_own_address(self):
        # #842: the native install does what the Docker install does: BOT_BOARD_ROUTINES=1, the runner told where the board is
        # and keeping its token in the board's data directory
        e = self.board_env(PORT=str(self.port))
        self.assertEqual(e, {"BOT_BOARD_ROUTINES": "1", "BOARD": f"http://127.0.0.1:{self.port}",
                             "ROUTINES_CONFIG_DIR": str(self.dir / "routines")})

    def test_an_all_interfaces_board_is_reached_by_the_runner_on_loopback(self):
        e = self.board_env(PORT=str(self.port), BIND="0.0.0.0")
        self.assertEqual(e["BOARD"], f"http://127.0.0.1:{self.port}")
        self.assertEqual(e["BOT_BOARD_ROUTINES"], "1")

    def test_routines_can_be_turned_off_for_a_board_whose_routines_run_elsewhere(self):
        self.assertEqual(self.board_env(PORT=str(self.port), BOT_BOARD_ROUTINES="0")["BOT_BOARD_ROUTINES"], "0")

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
        self.assertTrue("BIND=127.0.0.1" in r.stdout or "bot_board expose 127.0.0.1" in r.stdout, r.stdout)     # which one depends on whether bot_board is installed beside the checkout
        r = self.run_sh("status")            # the stored BIND: still said, though the address shows 127.0.0.1
        self.assertIn(f"running on http://127.0.0.1:{self.port}", r.stdout)
        self.assertIn("Bound to 0.0.0.0", r.stdout)
        self.run_sh("stop")
        self.run_sh("up", PORT=str(self.port), BIND="127.0.0.1", FAKE_COMMIT="ab;rm -rf \x1b[31mcd")      # the stored BIND stays until a run names another
        out = self.run_sh("status").stdout
        self.assertNotIn("Bound to", out, "loopback says nothing")
        self.assertIn("commit abrm-rf", out)       # the ; and the space are gone
        self.assertNotIn("\x1b", out, "only letters, digits, dot, dash and underscore of the commit are printed")

    def test_the_command_that_keeps_the_board_local_puts_BIND_on_the_right_program(self):
        body = subprocess.run(["sed", "-n", "/^tilde()/p;/^exposed()/,/^}/p", str(ROOT / "run.sh")], capture_output=True, text=True).stdout

        def say(src, self_):
            return subprocess.run(["bash", "-c", f'BIND=0.0.0.0 PORT=8080 SRC="{src}" SELF="$1"\n{body}\nexposed', "x", self_],
                                  capture_output=True, text=True, env={**os.environ, "HOME": self.tmp.name}).stdout
        curl = "curl -fsSL https://example.test/run.sh | bash -s --"
        out = say(self.tmp.name + "/none/src", curl)       # no installed bot_board beside this checkout: the curl form
        self.assertIn("run: curl -fsSL https://example.test/run.sh | BIND=127.0.0.1 bash -s -- up", out, "the variable is for bash, not curl")
        self.assertNotIn("BIND=127.0.0.1 curl", out)
        self.assertIn("run: BIND=127.0.0.1 /x/run.sh up", say(self.tmp.name + "/none/src", "/x/run.sh"))
        inst = Path(self.tmp.name, "inst"); (inst / "src").mkdir(parents=True); (inst / "bot_board").write_text("#!/bin/sh\n"); (inst / "bot_board").chmod(0o755)
        self.assertIn("run: ~/inst/bot_board expose 127.0.0.1", say(str(inst / "src"), curl), "an installed board has its own command, which moves the keeper and Forgejo with it")

    def test_a_start_that_never_answers_does_not_change_the_kept_address(self):      # #801
        self.assertEqual(self.run_sh("up", PORT=str(self.port)).returncode, 0)
        s = socket.socket(); s.bind(("127.0.0.1", 0)); other = s.getsockname()[1]; s.close()
        r = self.run_sh("up", PORT=str(other), FAKE_FAIL="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did not come up", r.stderr)
        self.assertEqual((self.dir / "board.addr").read_text(), f"PORT={self.port}\nBIND=127.0.0.1\n", "the address that answered stays")

    def test_a_port_in_the_file_that_is_not_a_number_is_not_used(self):
        self.dir.mkdir(exist_ok=True); (self.dir / "board.addr").write_text("PORT=80/../x\nBIND=127.0.0.1\n")
        r = self.run_sh("status")
        self.assertEqual((r.returncode, r.stdout), (1, "not running on http://127.0.0.1:8080\n"))

    def test_a_board_that_was_never_started_is_asked_about_on_the_default_port(self):
        r = self.run_sh("status")
        self.assertEqual((r.returncode, r.stdout), (1, "not running on http://127.0.0.1:8080\n"))


if __name__ == "__main__":
    unittest.main()
