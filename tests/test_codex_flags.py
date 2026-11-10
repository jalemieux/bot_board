"""The default codex flags (#582): with BOT_RUNTIME set the container is the sandbox, so codex runs without its own
(bubblewrap cannot start in a container); without it, codex keeps its workspace-write sandbox. A BOT_CODEX_FLAGS
set on the box or in the environment wins either way. Run with python -m unittest discover -s tests."""
import os
import subprocess
import unittest
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "bin" / "bot").read_text()
BLOCK = SOURCE[SOURCE.index("if [ -n \"${BOT_RUNTIME:-}\" ]; then\n  BOT_CODEX_FLAGS="):]
BLOCK = BLOCK[:BLOCK.index("\nfi\n") + 4]


def flags(**env):
    base = {k: v for k, v in os.environ.items() if k not in ("BOT_RUNTIME", "BOT_CODEX_FLAGS")}
    r = subprocess.run(["bash", "-c", BLOCK + '\nprintf "%s" "$BOT_CODEX_FLAGS"'], env={**base, **env},
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return r.stdout


class CodexFlags(unittest.TestCase):
    def test_in_a_container_codex_runs_without_its_own_sandbox(self):
        got = flags(BOT_RUNTIME="docker")
        self.assertEqual(got, "--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check")
        self.assertNotIn("workspace-write", got)

    def test_on_the_host_codex_keeps_its_workspace_write_sandbox(self):
        self.assertEqual(flags(), "-s workspace-write -c sandbox_workspace_write.network_access=true --skip-git-repo-check")

    def test_a_box_that_sets_its_own_flags_keeps_them_in_a_container_and_outside(self):
        self.assertEqual(flags(BOT_RUNTIME="docker", BOT_CODEX_FLAGS="-s read-only"), "-s read-only")
        self.assertEqual(flags(BOT_CODEX_FLAGS="--full-auto"), "--full-auto")

    def test_an_empty_runtime_is_not_a_container(self):
        self.assertIn("workspace-write", flags(BOT_RUNTIME=""))


if __name__ == "__main__":
    unittest.main()
