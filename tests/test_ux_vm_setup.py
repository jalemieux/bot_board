"""scripts/ux-vm/setup (#565), run against stand-ins for tart, ssh-keygen and build-base, in a scratch HOME.
Run with python -m unittest discover -s tests. It checks the script's logic and the lines it writes, not
that ssh enforces them: that needs a Mac with Remote Login on."""
import base64
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "ux-vm" / "setup"
UXVM = ROOT / "scripts" / "ux-vm" / "ux-vm"

FAKE_TART = """#!/usr/bin/env bash
case "$1" in get) [ -e "$FAKE/vm/$2" ] ;; esac
"""
FAKE_KEYGEN = """#!/usr/bin/env bash
while [ $# -gt 0 ]; do case $1 in -f) f=$2; shift ;; -C) c=$2; shift ;; esac; shift; done
printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\\nSECRETLINE-%s\\nSECOND-LINE\\n-----END OPENSSH PRIVATE KEY-----\\n' "$c" > "$f"
printf 'ssh-ed25519 AAAAFAKE%s %s\\n' "$c" "$c" > "$f.pub"
"""
FAKE_BUILD = """#!/usr/bin/env bash
echo "$*" >> "$FAKE/builds"
touch "$FAKE/vm/ux-base-$1"
"""


# A stand-in for ssh that answers as sshd would for the lines in authorized_keys: the control key gets ux-vm's
# usage, the jump key runs nothing (forced `false`), and a -W forward is allowed only to a literal permitopen entry,
# as OpenSSH does (no wildcard). It refuses a key whose from= does not name 127.0.0.1, and logs the from= it saw.
FAKE_SSH = r"""#!/usr/bin/env python3
import os, re, sys
a, key, w, rest, i = sys.argv[1:], None, None, [], 0
while i < len(a):
    if a[i] == "-i": key = a[i + 1]; i += 2
    elif a[i] == "-o": i += 2
    elif a[i] == "-W": w = a[i + 1]; i += 2
    else: rest.append(a[i]); i += 1
auth = open(os.path.join(os.environ["HOME"], ".ssh", "authorized_keys")).read().splitlines()
name = "control" if key.endswith("/control") else "jump"
line = [l for l in auth if l.endswith("ux-vm-" + name)][0]
frm = re.match(r'from="([^"]*)"', line)
open(os.path.join(os.environ["FAKE"], "sshlog"), "a").write(f"{name} from={frm.group(1) if frm else '-'} {' '.join(rest)} {w or ''}\n")
if frm and "127.0.0.1" not in frm.group(1).split(","):
    print("Permission denied (publickey).", file=sys.stderr); sys.exit(255)
if w:
    allowed = re.findall(r'permitopen="([^"]*)"', line)
    if w not in allowed and not os.environ.get("STUB_ALLOW_ALL"):
        print("channel 0: open failed: administratively prohibited: open failed", file=sys.stderr); sys.exit(255)
    print("connect failed: no route"); sys.exit(255)
if name == "control":
    print("usage: ux-vm reset linux | reset mac | stop | status", file=sys.stderr); sys.exit(2)
sys.exit(1)
"""


def permits(net):
    return ",".join(f'permitopen="{net}.{i}:22"' for i in range(2, 255))


def script(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.home = t / "home"
        self.home.mkdir()
        self.fake = t / "fake"
        (self.fake / "vm").mkdir(parents=True)
        bindir = t / "bin"
        bindir.mkdir()
        script(bindir / "tart", FAKE_TART)
        script(bindir / "ssh-keygen", FAKE_KEYGEN)
        script(bindir / "ssh", FAKE_SSH)
        script(t / "build-base", FAKE_BUILD)
        self.env = {**os.environ, "HOME": str(self.home), "FAKE": str(self.fake), "PATH": f"{bindir}:{os.environ['PATH']}",
                    "UX_BUILD_BASE": str(t / "build-base"), "UX_SETUP_PLATFORM": "Darwin arm64", "UX_SETUP_PROBE": "true"}
        self.keys = self.home / ".ux-vm" / "keys"
        self.auth = self.home / ".ssh" / "authorized_keys"

    def run_setup(self, *args, **env):
        return subprocess.run([str(SETUP), "--no-verify", *args], env={**self.env, **env}, capture_output=True,
                              text=True, timeout=60)

    def run_checked(self, *args, **env):       # with the checks, against the ssh stand-in
        return subprocess.run([str(SETUP), *args], env={**self.env, **env}, capture_output=True, text=True, timeout=60)

    def lines(self):
        return self.auth.read_text().splitlines()

    def test_a_machine_that_is_not_an_apple_silicon_mac_is_refused(self):
        r = self.run_setup(UX_SETUP_PLATFORM="Linux x86_64")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Apple Silicon", r.stderr)
        self.assertFalse(self.keys.exists())

    def test_the_first_run_makes_keys_builds_images_and_writes_two_restricted_lines(self):
        r = self.run_setup()
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("control", "jump"):
            self.assertEqual(stat.S_IMODE((self.keys / name).stat().st_mode), 0o600)
            b64 = (self.keys / f"{name}.b64").read_text()
            self.assertEqual(b64.count("\n"), 1, "one line: a multi-line value does not survive fleet.env")
            self.assertEqual(base64.b64decode(b64), (self.keys / name).read_bytes())
        self.assertEqual(stat.S_IMODE(self.keys.stat().st_mode), 0o700)
        self.assertEqual(sorted((self.fake / "builds").read_text().splitlines()),
                         [f"linux --pubkey {self.keys}/jump.pub", f"mac --pubkey {self.keys}/jump.pub"],
                         "the images get the jump key, so the keys came first")
        control, jump = self.lines()
        self.assertEqual(control, f'command="{UXVM}",restrict ssh-ed25519 AAAAFAKEux-vm-control ux-vm-control')
        self.assertEqual(jump, 'restrict,port-forwarding,' + permits("192.168.64") + ',command="/usr/bin/false" '
                               'ssh-ed25519 AAAAFAKEux-vm-jump ux-vm-jump')
        self.assertEqual(stat.S_IMODE(self.auth.stat().st_mode), 0o600)
        self.assertIn("done", r.stdout)

    def test_the_jump_key_has_a_forced_command_because_restrict_alone_still_runs_commands(self):
        self.run_setup()
        jump = self.lines()[1]
        self.assertIn('command="/usr/bin/false"', jump)
        self.assertIn("port-forwarding", jump)

    def test_permitopen_lists_each_vm_address_because_openssh_does_not_match_a_wildcard(self):
        self.run_setup()
        jump = self.lines()[1]
        opens = [o for o in jump.split(",") if o.startswith("permitopen=")]
        self.assertEqual(len(opens), 253)
        self.assertNotIn("*", "".join(opens))
        self.assertIn('permitopen="192.168.64.2:22"', opens)
        self.assertIn('permitopen="192.168.64.254:22"', opens)
        self.assertNotIn('permitopen="192.168.64.1:22"', opens, "the Mac itself stays out of reach")
        self.assertNotIn('permitopen="192.168.64.255:22"', opens)
        self.assertLess(len(jump), 9000, "one line sshd accepts")

    def test_another_tart_network_can_be_given(self):
        self.run_setup(UX_VM_NET="10.1.2")
        self.assertIn('permitopen="10.1.2.2:22"', self.lines()[1])
        self.assertNotIn("192.168.64", self.lines()[1])

    def test_a_path_with_a_space_or_a_quote_is_refused_because_sshd_runs_it_through_a_shell(self):
        for name in ("dir with space", 'dir"quote', "dir;semi", "dir$var"):
            copy = Path(self.tmp.name) / name / "ux-vm"
            copy.mkdir(parents=True)
            (copy / "setup").write_bytes(SETUP.read_bytes())      # only setup: it never opens ux-vm, it writes its path
            (copy / "setup").chmod(0o755)
            r = subprocess.run([str(copy / "setup"), "--no-verify"], env=self.env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, name)
            self.assertIn("will not put into authorized_keys", r.stderr)
        self.assertFalse(self.auth.exists())

    def test_from_refuses_a_newline_a_space_and_a_quote(self):
        for bad in ("a\nb", "a b", 'a"b', "a;b", "$(id)"):
            r = self.run_setup("--from", bad)
            self.assertEqual(r.returncode, 1, repr(bad))
        self.assertFalse(self.auth.exists())

    def test_no_private_key_is_ever_printed(self):
        r = self.run_setup()
        everything = r.stdout + r.stderr
        for name in ("control", "jump"):
            self.assertNotIn("SECRETLINE", everything)
            self.assertNotIn((self.keys / f"{name}.b64").read_text().strip(), everything)
        self.assertIn("UX_SSH_CONTROL_KEY", r.stdout)
        self.assertIn(str(self.keys / "jump.b64"), r.stdout)

    def test_a_second_run_changes_nothing_and_says_so(self):
        self.run_setup()
        before = (self.auth.read_text(), (self.keys / "control").read_bytes(), (self.fake / "builds").read_text())
        r = self.run_setup()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing changed", r.stdout)
        self.assertEqual(before, (self.auth.read_text(), (self.keys / "control").read_bytes(), (self.fake / "builds").read_text()))

    def test_from_limits_both_keys_and_a_changed_from_replaces_the_lines_and_keeps_others(self):
        self.auth.parent.mkdir()
        self.auth.write_text("ssh-rsa AAAAother someone@else\n")
        self.run_setup("--from", "100.64.0.9")
        lines = self.lines()
        self.assertEqual(lines[0], "ssh-rsa AAAAother someone@else")
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(l.startswith('from="100.64.0.9",') for l in lines[1:]))
        self.assertTrue((self.home / ".ssh" / "authorized_keys.ux-vm.bak").exists())
        r = self.run_setup("--from", "100.64.0.10,100.64.0.11")
        lines = self.lines()
        self.assertEqual(len(lines), 3, "the two lines are replaced, not added again")
        self.assertTrue(all('from="100.64.0.10,100.64.0.11"' in l for l in lines[1:]))
        self.assertEqual(lines[0], "ssh-rsa AAAAother someone@else")
        self.assertNotIn("no --from", r.stderr)

    def test_without_from_it_warns(self):
        self.assertIn("no --from", self.run_setup().stderr)

    def test_remote_login_off_is_reported_but_the_lines_are_still_installed(self):
        r = self.run_setup(UX_SETUP_PROBE="false")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Remote Login is off", r.stderr)
        self.assertEqual(len(self.lines()), 2)

    def test_a_quote_in_from_is_refused(self):
        r = self.run_setup("--from", 'x" command="evil')
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.auth.exists())

    def test_the_checks_pass_on_the_lines_it_writes_and_tell_refused_from_allowed(self):
        r = self.run_checked()
        self.assertEqual(r.returncode, 0, r.stderr)
        for ok in ("the control key runs ux-vm and nothing else", "the jump key runs no command",
                   "the jump key may not forward to 127.0.0.1", "the jump key may not forward to 192.168.64.1",
                   "the jump key may forward to a VM address"):
            self.assertIn("check ok: " + ok, r.stdout)
        log = (self.fake / "sshlog").read_text()
        self.assertIn("192.168.64.2:22", log)

    def test_a_forward_that_is_not_refused_fails_the_check_and_the_run(self):
        r = self.run_checked(STUB_ALLOW_ALL="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("was not refused a forward to", r.stderr)
        self.assertIn("a check failed", r.stderr)

    def test_with_from_the_checks_run_on_lines_limited_to_this_mac_and_the_real_lines_follow(self):
        r = self.run_checked("--from", "100.64.0.9")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("checks skipped", r.stdout)
        self.assertTrue(all("from=127.0.0.1" in l for l in (self.fake / "sshlog").read_text().splitlines()))
        self.assertTrue(all(l.startswith('from="100.64.0.9",') for l in self.lines()), "the real lines are installed")
        again = self.run_checked("--from", "100.64.0.9")
        self.assertIn("nothing changed", again.stdout)

    def test_a_failed_check_with_from_leaves_the_lines_limited_to_this_mac(self):
        r = self.run_checked("--from", "100.64.0.9", STUB_ALLOW_ALL="1")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(all(l.startswith('from="127.0.0.1",') for l in self.lines()))
        self.assertIn("limited to 127.0.0.1", r.stderr)

    def test_existing_images_are_not_rebuilt(self):
        for os_ in ("linux", "mac"):
            (self.fake / "vm" / f"ux-base-{os_}").write_text("")
        r = self.run_setup()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.fake / "builds").exists())
        self.assertIn("the base images exist", r.stdout)


if __name__ == "__main__":
    unittest.main()
