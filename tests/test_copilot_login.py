"""#285: the fleet's GH_TOKEN is for gh; copilot gets COPILOT_GITHUB_TOKEN when it is set, and
otherwise runs without GH_TOKEN, so copilot's own login is used, while the gh it runs still has
the token. Run with python -m unittest discover -s tests."""
import json
import stat
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from fakes import harness as fh                  # noqa: E402
from fakes.board import FakeBoard                # noqa: E402
from test_launcher_e2e import Launcher           # noqa: E402


class CopilotLogin(unittest.TestCase):
    def run_once(self, env):
        with FakeBoard() as board:
            L = Launcher(board, "copilot", env=env)
            for k in ("GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN"):     # only what the test gives
                if k not in env:
                    L.env.pop(k, None)
            try:
                L.plan(fh.step("copilot", "ok", "rules read"))
                L.start()
                L.wait_for(lambda: L.read("runs") == "1", "the first turn")
                L.stop()
                seen = json.loads((L.scenario / "copilot.env").read_text().splitlines()[-1])
                hosts = L.state / "gh" / "hosts.yml"
                return seen, (hosts.read_text(), stat.S_IMODE(hosts.stat().st_mode)) if hosts.exists() else None, L.state
            finally:
                L.close()

    def test_without_a_copilot_token_gh_token_does_not_reach_copilot_but_its_gh_has_it(self):
        seen, hosts, state = self.run_once({"GH_TOKEN": "ghp_fleet", "GITHUB_TOKEN": "ghp_other"})
        self.assertIsNone(seen["GH_TOKEN"])
        self.assertIsNone(seen["GITHUB_TOKEN"])
        self.assertEqual(seen["GH_CONFIG_DIR"], str(state / "gh"))
        self.assertIn("oauth_token: ghp_fleet", hosts[0])
        self.assertEqual(hosts[1], 0o600)

    def test_a_copilot_token_is_used_and_gh_keeps_gh_token(self):
        seen, hosts, _ = self.run_once({"GH_TOKEN": "ghp_fleet", "COPILOT_GITHUB_TOKEN": "github_pat_copilot"})
        self.assertEqual(seen["COPILOT_GITHUB_TOKEN"], "github_pat_copilot")
        self.assertEqual(seen["GH_TOKEN"], "ghp_fleet")
        self.assertIsNone(seen["GH_CONFIG_DIR"])
        self.assertIsNone(hosts)

    def test_no_token_at_all_changes_nothing(self):
        seen, hosts, _ = self.run_once({})
        self.assertEqual(seen, {"GH_TOKEN": None, "GITHUB_TOKEN": None, "COPILOT_GITHUB_TOKEN": None, "GH_CONFIG_DIR": None})
        self.assertIsNone(hosts)


if __name__ == "__main__":
    unittest.main()
