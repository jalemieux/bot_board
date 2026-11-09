"""scripts/ux-vm/ux-vm (#564): its action parsing, and reset, stop and status against a stand-in for `tart`.
Run with python -m unittest discover -s tests. The real Tart is not involved: this checks the script's
logic on any machine with bash, not that a VM boots."""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UXVM = ROOT / "scripts" / "ux-vm" / "ux-vm"
BUILD = ROOT / "scripts" / "ux-vm" / "build-base"

# A stand-in for tart. State lives in $FAKE as files: one per VM, and a call log.
FAKE_TART = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE/calls"
case "$1" in
  get)    [ -e "$FAKE/vm/$2" ] ;;
  clone)  case "$2" in ghcr.io/*) ;; *) [ -e "$FAKE/vm/$2" ] || { echo "no such image $2" >&2; exit 1; } ;; esac
          echo "$2" > "$FAKE/vm/$3" ;;
  rename) mv "$FAKE/vm/$2" "$FAKE/vm/$3" ;;
  run)    [ -n "$FAKE_RUN_FAILS" ] && { echo "Virtualization.Error: limit of 2 macOS VMs" >&2; exit 1; }
          touch "$FAKE/running/$3"; sleep "${FAKE_RUN_SECONDS:-30}" ;;
  ip)     [ -e "$FAKE/running/$2" ] && [ -z "$FAKE_NO_IP" ] && echo 192.168.64.7 ;;
  stop)   rm -f "$FAKE/running/$2" ;;
  delete) rm -f "$FAKE/vm/$2" ;;
  exec)   shift; flag=""; [ "$1" = "-i" ] && { flag=i; shift; }; vm=$1; shift
          if [ -n "$FAKE_EXEC_FAILS" ] && [[ "$*" == *"$FAKE_EXEC_FAILS"* ]]; then echo "exec failed: $*" >&2; exit 1; fi
          case "$*" in
            *"cat >> ~/.ssh/authorized_keys"*)   # stdin arrives only with -i, as with the real tart exec
              if [ -n "$flag" ] && [ -z "$FAKE_NO_STDIN" ]; then cat >> "$FAKE/keys-$vm"; else cat >/dev/null; fi ;;
            *"grep -qF"*) grep -qF -- "${@: -1}" "$FAKE/keys-$vm" 2>/dev/null ;;
          esac ;;
esac
"""


class UxVm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.fake = t / "fake"
        for d in ("vm", "running"):
            (self.fake / d).mkdir(parents=True)
        for base in ("ux-base-linux", "ux-base-mac"):
            (self.fake / "vm" / base).write_text("")
        self.tart = t / "tart"
        self.tart.write_text(FAKE_TART)
        self.tart.chmod(self.tart.stat().st_mode | stat.S_IEXEC)
        self.env = {**os.environ, "TART": str(self.tart), "FAKE": str(self.fake), "UX_VM_STATE": str(t / "state"),
                    "UX_VM_PROBE": "true", "UX_VM_WAIT": "6"}
        self.env.pop("SSH_ORIGINAL_COMMAND", None)

    def run_ux(self, *args, ssh=None, **env):
        e = {**self.env, **env}
        if ssh is not None:
            e["SSH_ORIGINAL_COMMAND"] = ssh
        return subprocess.run([str(UXVM), *args], env=e, capture_output=True, text=True, timeout=60)

    def calls(self):
        p = self.fake / "calls"
        return p.read_text().splitlines() if p.exists() else []

    def test_reset_prints_only_the_ip_and_leaves_a_running_vm(self):
        r = self.run_ux("reset", "linux")
        self.assertEqual((r.returncode, r.stdout), (0, "192.168.64.7\n"), r.stderr)
        self.assertIn("clone ux-base-linux ux-run", self.calls())
        self.assertTrue((self.fake / "running" / "ux-run").exists())
        self.assertIn("run --no-graphics ux-run", self.calls())

    def test_the_action_comes_from_the_ssh_forced_command(self):
        r = self.run_ux(ssh="reset mac")
        self.assertEqual(r.stdout, "192.168.64.7\n", r.stderr)
        self.assertIn("clone ux-base-mac ux-run", self.calls())

    def test_a_second_reset_starts_from_a_fresh_clone(self):
        self.run_ux("reset", "linux")
        before = len(self.calls())
        self.run_ux("reset", "linux")
        after = self.calls()[before:]
        self.assertEqual([c.split()[0] for c in after if c.split()[0] in ("stop", "delete", "clone")],
                         ["stop", "delete", "clone"], "the old VM goes before the new one is cloned")

    def test_anything_but_the_three_actions_is_refused_and_touches_nothing(self):
        for bad in ("reset linux; rm -rf ~", "reset  linux", "reset linux && id", "reset", "reset windows",
                    "status extra", "stop now", "$(id)", "`id`", "reset linux\nid", "RESET LINUX", ""):
            r = self.run_ux(ssh=bad)
            self.assertEqual(r.returncode, 2, repr(bad))
            self.assertEqual(r.stdout, "", repr(bad))
            self.assertIn("usage", r.stderr)
        self.assertFalse((self.fake / "calls").exists(), "a refused action never reaches tart")

    def test_the_arguments_are_one_string_too(self):
        r = self.run_ux("reset linux; id")
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.fake / "calls").exists())

    def test_a_missing_base_image_says_how_to_build_it(self):
        (self.fake / "vm" / "ux-base-mac").unlink()
        r = self.run_ux("reset", "mac")
        self.assertEqual(r.returncode, 1)
        self.assertIn("build-base mac", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_a_tart_run_that_dies_is_reported_not_waited_for(self):
        r = self.run_ux("reset", "mac", FAKE_RUN_FAILS="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("tart run stopped early", r.stderr)
        self.assertIn("limit of 2 macOS VMs", r.stderr)

    def test_a_vm_that_never_answers_is_stopped_after_the_wait(self):
        r = self.run_ux("reset", "linux", FAKE_NO_IP="1", UX_VM_WAIT="2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("did not answer", r.stderr)
        self.assertFalse((self.fake / "running" / "ux-run").exists())

    def test_stop_and_status(self):
        self.assertEqual(self.run_ux("status").stdout, "no run VM\n")
        self.run_ux("reset", "linux")
        self.assertEqual(self.run_ux("status").stdout, "ux-run: running, base ux-base-linux, ip 192.168.64.7\n")
        r = self.run_ux(ssh="stop")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse((self.fake / "vm" / "ux-run").exists())
        self.assertEqual(self.run_ux("status").stdout, "no run VM\n")

    def test_the_ssh_session_is_not_held_open_by_the_vm(self):
        # the VM keeps running after reset returns; reset must not wait on it or on its output
        import time
        start = time.time()
        r = self.run_ux("reset", "linux", FAKE_RUN_SECONDS="25")
        self.assertEqual(r.returncode, 0)
        self.assertLess(time.time() - start, 15, "reset waited for the VM's own process")

    def test_build_base_needs_a_key_and_leaves_existing_images_alone(self):
        r = subprocess.run([str(BUILD), "linux"], env={**self.env, "HOME": self.tmp.name}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no public key", r.stderr)
        key = Path(self.tmp.name) / "k.pub"
        key.write_text("ssh-ed25519 AAAA test\n")
        r = subprocess.run([str(BUILD), "linux", "--pubkey", str(key)], env={**self.env, "HOME": self.tmp.name},
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("left alone", r.stderr)
        self.assertEqual([c for c in self.calls() if c.startswith(("clone", "run", "delete"))], [])
        r = subprocess.run([str(BUILD), "bogus"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("usage", r.stderr)

    def build(self, *extra, **env):
        key = Path(self.tmp.name) / "k.pub"
        key.write_text("ssh-ed25519 AAAAkey jump\n")
        e = {**self.env, "HOME": self.tmp.name, "FAKE_RUN_SECONDS": "2", **env}
        return subprocess.run([str(BUILD), "linux", "--pubkey", str(key), *extra], env=e, capture_output=True,
                              text=True, timeout=60)

    def images(self):
        return sorted(p.name for p in (self.fake / "vm").iterdir())

    def test_a_build_makes_the_image_under_a_temporary_name_and_renames_it_at_the_end(self):
        (self.fake / "vm" / "ux-base-linux").unlink()
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.images(), ["ux-base-linux", "ux-base-mac"])      # no -building left
        self.assertIn("rename ux-base-linux-building ux-base-linux", self.calls())
        self.assertIn("exec -i ux-base-linux-building sh -c cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys",
                      self.calls(), "the key goes in over stdin, which tart exec forwards only with -i")
        self.assertIn("ssh-ed25519 AAAAkey jump", (self.fake / "keys-ux-base-linux-building").read_text())

    def test_a_failed_build_leaves_no_image_for_the_next_run_to_skip(self):
        (self.fake / "vm" / "ux-base-linux").unlink()
        r = self.build(FAKE_EXEC_FAILS="apt-get")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.images(), ["ux-base-mac"], "neither a half-built image nor a -building one remains")
        self.assertIn("removing the unfinished ux-base-linux-building", r.stderr)
        r = self.build()                                            # the next run builds it, not skips it
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("left alone", r.stderr)
        self.assertEqual(self.images(), ["ux-base-linux", "ux-base-mac"])

    def test_a_key_that_did_not_land_stops_the_build(self):
        (self.fake / "vm" / "ux-base-linux").unlink()
        r = self.build(FAKE_NO_STDIN="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("jump key did not reach", r.stderr)
        self.assertEqual(self.images(), ["ux-base-mac"])

    def test_a_forced_rebuild_that_fails_keeps_the_old_image(self):
        r = self.build("--force", FAKE_EXEC_FAILS="apt-get")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.images(), ["ux-base-linux", "ux-base-mac"])


if __name__ == "__main__":
    unittest.main()
