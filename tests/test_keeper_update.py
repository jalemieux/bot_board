"""#107: the keeper keeps its checkout current with its origin, checks the new code, rolls back
what fails, and asks the running bots to restart on what passes. Run with
python -m unittest discover -s tests."""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GOOD_BOT = '''#!/usr/bin/env bash
# a stand-in for bin/bot: version answers, restart records who was asked
case ${1:-} in
  version) echo "bot test"; exit 0 ;;
  restart) echo "$2" >> "$(dirname "$0")/../restarted"; echo "bot: $2 is restarting"; exit 0 ;;
esac
exit 0
'''
GOOD_KEEPER = "import sys\n"


def sh(*cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def load_keeper(home, **env):
    saved = {k: os.environ.get(k) for k in ("HOME", "BOT_CONFIG", *env)}
    os.environ.update(HOME=home, BOT_CONFIG=os.path.join(home, "config"), **env)
    try:
        loader = importlib.machinery.SourceFileLoader("keeper_u", str(ROOT / "bin" / "keeper"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("keeper_u", loader))
        loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    return mod


class Update(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.origin = home / "origin.git"; sh("git", "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.work = home / "work"; sh("git", "clone", "-q", str(self.origin), str(self.work))
        self.git_env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        self.commit("v1", GOOD_BOT, GOOD_KEEPER)
        self.src = home / "src"; sh("git", "clone", "-q", str(self.origin), str(self.src))
        (self.src / "bin/bot").chmod(0o755)
        os.environ.pop("BOT_UPDATE", None); os.environ.pop("BOT_UPDATE_REF", None)
        self.k = load_keeper(self.tmp.name)

    def commit(self, msg, bot=None, keeper=None, tag=None):
        (self.work / "bin").mkdir(exist_ok=True)
        if bot is not None: (self.work / "bin/bot").write_text(bot); (self.work / "bin/bot").chmod(0o755)
        if keeper is not None: (self.work / "bin/keeper").write_text(keeper)
        env = {**os.environ, **self.git_env}
        subprocess.run(["git", "add", "-A"], cwd=self.work, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=self.work, check=True, capture_output=True, env=env)
        if tag: subprocess.run(["git", "tag", tag], cwd=self.work, check=True, capture_output=True, env=env)
        subprocess.run(["git", "push", "-q", "--tags", "origin", "main"], cwd=self.work, check=True, capture_output=True)
        return sh("git", "rev-parse", "HEAD", cwd=self.work)

    def updater(self, **settings):
        for k in ("BOT_UPDATE", "BOT_UPDATE_REF"): os.environ.pop(k, None)
        os.environ.update(settings); self.addCleanup(lambda: [os.environ.pop(k, None) for k in settings])
        return self.k.Updater(src=str(self.src), bot=str(self.src / "bin/bot"))

    def head(self):
        return sh("git", "rev-parse", "HEAD", cwd=self.src)

    BOTS = [{"handle": "ada", "status": "waiting"}, {"handle": "milo", "status": "running ws"}, {"handle": "old", "status": "stopped"}]

    def test_nothing_to_do_when_up_to_date(self):
        u = self.updater()
        self.assertFalse(u.tick(self.BOTS, force=True))
        self.assertEqual(u.state["result"], "up to date")
        self.assertTrue(u.state["checked"])
        self.assertFalse((self.src / "restarted").exists())

    def test_a_new_commit_is_pulled_checked_and_the_running_bots_restart(self):
        old = self.head()
        new = self.commit("v2", GOOD_BOT + "# v2\n")
        u = self.updater()
        self.assertTrue(u.tick(self.BOTS, force=True))
        self.assertEqual(self.head(), new)
        self.assertEqual(u.state["result"], f"updated {old[:12]} -> {new[:12]}")
        self.assertEqual((self.src / "restarted").read_text().split(), ["ada", "milo"], "stopped bots are not asked")
        self.assertTrue(u.code_changed(), "the keeper's own code moved: it should start again")
        self.assertFalse(u.tick(self.BOTS, force=True), "a second look finds nothing")
        self.assertEqual(u.state["result"], "up to date")

    def test_code_that_fails_its_checks_is_rolled_back_and_not_retried(self):
        old = self.head()
        bad = self.commit("v2 broken", "#!/usr/bin/env bash\nif then fi\n")
        u = self.updater()
        self.assertFalse(u.tick(self.BOTS, force=True))
        self.assertEqual(self.head(), old, "the previous commit is back")
        self.assertIn("rolled back", u.state["result"])
        self.assertIn(bad[:12], u.state["result"])
        self.assertEqual(u.state["revision"], sh("git", "describe", "--tags", "--always", cwd=self.src))
        self.assertFalse((self.src / "restarted").exists())
        self.assertFalse(u.code_changed())
        u.checked = 0
        self.assertFalse(u.tick(self.BOTS), "the same bad commit is not tried again on its own")
        self.assertIn("rolled back", u.state["result"])
        good = self.commit("v3", GOOD_BOT)
        u.checked = 0
        self.assertTrue(u.tick(self.BOTS), "a newer commit is tried")
        self.assertEqual(self.head(), good)

    def test_a_keeper_that_does_not_compile_is_rolled_back_too(self):
        old = self.head()
        self.commit("v2 bad keeper", keeper="def (:\n")
        u = self.updater()
        self.assertFalse(u.tick([], force=True))
        self.assertEqual(self.head(), old)
        self.assertIn("py_compile", u.state["result"])

    def test_local_changes_are_left_alone(self):
        (self.src / "bin/bot").write_text(GOOD_BOT + "# mine\n")
        self.commit("v2", GOOD_BOT + "# v2\n")
        u = self.updater()
        self.assertFalse(u.tick(self.BOTS, force=True))
        self.assertIn("local changes", u.state["result"])
        self.assertIn("# mine", (self.src / "bin/bot").read_text())

    def test_updates_can_be_turned_off(self):
        self.commit("v2", GOOD_BOT + "# v2\n")
        u = self.updater(BOT_UPDATE="off")
        self.assertFalse(u.tick(self.BOTS, force=True))
        self.assertIn("off", u.state["result"])

    def test_a_pinned_ref_is_followed_instead_of_the_branch(self):
        pinned = self.commit("v2", GOOD_BOT + "# v2\n", tag="v9.9.9")
        self.commit("v3", GOOD_BOT + "# v3\n")
        u = self.updater(BOT_UPDATE_REF="v9.9.9")
        self.assertTrue(u.tick(self.BOTS, force=True))
        self.assertEqual(self.head(), pinned)
        self.assertFalse(u.tick(self.BOTS, force=True))
        self.assertEqual(u.state["result"], "up to date")

    def test_checks_are_not_due_until_the_interval_has_passed(self):
        self.commit("v2", GOOD_BOT + "# v2\n")
        u = self.updater(BOT_UPDATE_EVERY="3600")
        u.checked = __import__("time").time()
        self.assertFalse(u.tick(self.BOTS))
        self.assertEqual(u.state["result"], "", "no check ran")

    def test_the_report_carries_the_updater_state(self):
        u = self.updater(); u.tick([], force=True)
        self.assertEqual(set(u.state), {"revision", "checked", "result", "available"})
        self.assertNotIn("", (u.state["revision"], u.state["checked"]))


if __name__ == "__main__":
    unittest.main()
