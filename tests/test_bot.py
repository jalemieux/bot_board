import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CopilotLauncherTests(unittest.TestCase):
    def test_permissions_on_first_and_resumed_turns(self):
        source = (ROOT / "bin/bot").read_text()
        default = next(
            line for line in source.splitlines()
            if line.startswith("BOT_COPILOT_FLAGS=")
        )
        runner = source.split("run_harness() {", 1)[1].split(
            "\n# ---- where a message", 1
        )[0]
        script = default + "\nrun_harness() {" + runner + """
HARNESS=copilot
STATE=$TEST_STATE
LOG=$STATE/log
load_fleet_env() { :; }
said_limited() { :; }
copilot() { printf '%s\\n' "$@" >> "$STATE/args"; }
run_harness "$STATE" "$STATE/session" "first turn"
run_harness "$STATE" "$STATE/session" "resumed turn"
"""
        for override in (None, "--allow-all-tools --add-dir /tokens --allow-url localhost"):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as tmp:
                env = {**os.environ, "TEST_STATE": tmp}
                env.pop("BOT_COPILOT_FLAGS", None)
                if override is not None:
                    env["BOT_COPILOT_FLAGS"] = override
                subprocess.run(
                    ["bash", "-euc", script], env=env, check=True,
                    capture_output=True, text=True,
                )
                state = Path(tmp)
                sid = (state / "session").read_text().strip()
                flags = (override or "--allow-all").split()
                self.assertEqual(
                    (state / "args").read_text().splitlines(),
                    [
                        "-s", "--session-id", sid, *flags, "-p", "first turn",
                        "-s", "--resume", sid, *flags, "-p", "resumed turn",
                    ],
                )
                self.assertEqual((state / "runs").read_text().strip(), "2")


if __name__ == "__main__":
    unittest.main()
