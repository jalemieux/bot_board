"""#857: a routine's check runs in a scrubbed environment: the plumbing, the fleet's secrets and its own variables, and not the
rest of the runner's process (which, in the board, holds its database path and whatever the person exported).
Run with python -m unittest discover -s tests."""
import importlib.machinery
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROUTINES = str(Path(__file__).resolve().parents[1] / "bin" / "routines")
SCRIPT = "#!/usr/bin/env python3\nimport json, os\nprint(json.dumps({'work': False, 'env': dict(os.environ)}))\n"


def load_cli():
    loader = importlib.machinery.SourceFileLoader("routines_cli_env", ROUTINES)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


class CheckEnv(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.saved)))

    def run_check(self, **extra):
        os.environ.update({"BOT_BOARD_DB": "/var/board.db", "BOT_BOARD_INVITE_CODE": "open-sesame", "SOMETHING_EXPORTED": "x",
                           "GH_TOKEN": "from-the-process", **extra})
        self.cli.secrets = lambda: {"GH_TOKEN": "fleet-gh", "FORGEJO_TOKEN": "fleet-fj"}
        return self.cli.run_check(SCRIPT, "name-1", {"sent": [1]})["env"]

    def test_a_variable_set_in_the_runners_environment_is_absent_from_the_check(self):
        env = self.run_check()
        for k in ("BOT_BOARD_DB", "BOT_BOARD_INVITE_CODE", "SOMETHING_EXPORTED"):
            self.assertNotIn(k, env)

    def test_the_fleet_secrets_win_and_the_routines_own_variables_are_there(self):
        env = self.run_check()
        self.assertEqual((env["GH_TOKEN"], env["FORGEJO_TOKEN"]), ("fleet-gh", "fleet-fj"))
        self.assertEqual(env["ROUTINE_NAME"], "name-1")
        self.assertEqual(json.loads(env["ROUTINE_WATERMARK"]), {"sent": [1]})
        self.assertEqual(env["BOARD"], self.cli.BOARD)

    def test_a_runner_that_is_not_the_board_hands_on_the_tokens_the_shipped_checks_read(self):
        self.cli.secrets = lambda: {}
        os.environ.update({"FORGEJO_TOKEN": "box-fj", "GH_TOKEN": "box-gh", "BOT_BOARD_DB": "/x"})
        env = self.cli.run_check(SCRIPT, "n")["env"]
        self.assertEqual((env["FORGEJO_TOKEN"], env["GH_TOKEN"]), ("box-fj", "box-gh"))
        self.assertNotIn("BOT_BOARD_DB", env)

    def test_the_plumbing_a_program_needs_is_kept(self):
        env = self.run_check(LANG="C.UTF-8", HTTPS_PROXY="http://proxy:3128", ROUTINE_NOW="2026-11-25T08:00:00+00:00")
        self.assertEqual(env["PATH"], os.environ["PATH"])
        self.assertEqual((env["LANG"], env["HTTPS_PROXY"]), ("C.UTF-8", "http://proxy:3128"))
        self.assertEqual(env["ROUTINE_NOW"], "2026-11-25T08:00:00+00:00")
        self.assertIn("HOME", env)


if __name__ == "__main__":
    unittest.main()
