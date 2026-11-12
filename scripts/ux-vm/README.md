# ux-vm: a throwaway VM for the UX bot

The UX bot (`user-advocate`) acts as a first-time user: it installs bot_board on a clean machine and starts
bots. Its test machine is a VM on an Apple Silicon Mac, managed with [Tart](https://tart.run). `ux-vm` is the one
control the bot has over it.

## ux-vm

```sh
scripts/ux-vm/ux-vm reset linux    # or: reset mac
scripts/ux-vm/ux-vm status
scripts/ux-vm/ux-vm stop
```

| Action | What it does |
|---|---|
| `reset linux` / `reset mac` | Stops and deletes the run VM `ux-run` if there is one, clones `ux-base-linux` or `ux-base-mac` into a new `ux-run`, boots it headless (`tart run --no-graphics`) in the background, waits until ssh answers on port 22, and prints the VM's IP on stdout, nothing else. |
| `stop` | Stops and deletes `ux-run`. |
| `status` | Prints `no run VM`, or the VM's base image and IP, or that it exists but is not running. |

Anything else prints a usage line and exits 2. The text is compared as a whole against these four strings and is
never passed to a shell, so `reset linux; rm -rf ~` is refused.

Run through ssh with a forced command (`command="…/ux-vm",restrict` in `authorized_keys`), the action is read from
`$SSH_ORIGINAL_COMMAND`. Run by hand, it is read from the arguments. Only one run VM exists at a time. Apple allows two
macOS guests running at once on a host; if that limit is hit, `reset mac` fails with Tart's own message.

**How long a reset takes.** Cloning an image is close to instant. Booting and getting an IP takes longer, and it is
not measured yet: the script waits up to 300 seconds (`UX_VM_WAIT`) and then stops the VM and says so. Fill in the
real time here after the first run on the Mac.

**Things to know before the key is installed**

- `TART`, `UX_VM_STATE`, `UX_VM_PROBE` and `UX_VM_WAIT` are read from the environment. The control key's login must not be
  able to set them. sshd's default does not: `PermitUserEnvironment no`, and `AcceptEnv` only `LANG` and `LC_*`. Do not turn either
  on for this account.
- There is no lock. Two sessions that run `reset` at once race on `ux-run`. The bot runs one journey at a time, so this is
  left as it is.
- A forced command gets a short `PATH`. The script adds `/opt/homebrew/bin` and `/usr/local/bin`, and says so if `tart` is still
  not found.

## build-base

```sh
scripts/ux-vm/build-base            # both images
scripts/ux-vm/build-base linux      # one
scripts/ux-vm/build-base mac --pubkey ~/.ssh/ux_jump.pub --force
```

Builds `ux-base-linux` (from `ghcr.io/cirruslabs/ubuntu:latest`) and `ux-base-mac` (from `ghcr.io/cirruslabs/macos-sequoia-base:latest`). Both names were checked against the registry on 2026-11-11 and still exist, although Tart itself has moved to OpenAI; `UX_MAC_SRC` and `UX_LINUX_SRC` override them (for example `ghcr.io/cirruslabs/macos-tahoe-base:latest`). Each gets
the bot's jump key on the `admin` user, Node, Playwright and Chromium, and nothing from bot_board: installing bot_board
is what the journeys test. An image that exists is left alone unless `--force`. An image is built under a temporary name (`ux-base-<os>-building`) and renamed at the end, so a failed build leaves nothing for the next run to take as finished; a failed `--force` build keeps the old image. The key goes in over stdin with `tart exec -i`, and the build checks that it landed. The first build downloads several GB.

The Linux image gets Node from the official tarball, pinned to v22.23.4 (the LTS at the time of writing) and to its published
sha256, because Ubuntu's apt Node is 18 and Playwright needs 20 or newer (the first build on a Mac failed on that); override with
`UX_NODE_VERSION`, `UX_NODE_SHA_ARM64` and `UX_NODE_SHA_X64`. The tarball URL, the hash and the unpacked layout were checked
on Linux x64; the arm64 file was checked to exist, not unpacked.

Known limits, not yet checked on a Mac: (1) the arm64 Node tarball, as said above. (2) Remote Login is turned on in the macOS image with `systemsetup`, which
may need Full Disk Access and fail through the guest agent; the Cirrus images already accept ssh. (3) Both images keep the
default `admin` / `admin` ssh password. They are reachable only from the Mac, but do not assume only the key can log in.
(4) Whether `tart run` of a macOS guest works from an ssh session with the screen locked or the user logged out is not known;
test it once and write the answer here.

## What was tested

`tests/test_ux_vm.py` runs `ux-vm` against a stand-in for `tart`: the action parsing and refusals, reset, a second
reset starting from a fresh clone, stop, status, a missing base image, a VM that dies or never answers, and `build-base`:
building under a temporary name and renaming, a failed build leaving no image, the key check, a failed `--force` build keeping the old image, and existing images left alone. It does **not** run Tart. `build-base` and a real reset have not been run on a Mac, and
`build-base` assumes the Cirrus images' guest agent (`tart exec`) and their `admin` user.

## setup: prepare the Mac once

```sh
scripts/ux-vm/setup --from <the bot box's tailnet address or CIDR>
```

Run it on the Mac that will host the VMs. It installs Tart with Homebrew if missing (`brew install openai/tools/tart`: Tart moved to the `openai/tools` tap, and the old Cirrus Labs tap no longer has it; if brew says the tap is not trusted, run `brew trust openai/tools` and run setup again), makes two ed25519 keys under
`~/.ux-vm/keys`, builds the base images if they are missing (the images hold the jump key, so the keys come first),
checks that Remote Login is on, writes two restricted lines to `~/.ssh/authorized_keys`, checks them, and prints what to
do next. A second run changes nothing and says so. `--from` limits both keys to the bot box (a single address, a list, or
a CIDR such as `100.64.0.0/10`) and is recommended; without it the script warns. `--no-verify` skips the checks.

The two lines:

| Key | Line options | What it can do |
|---|---|---|
| control | `command="<path>/ux-vm",restrict` | Run `ux-vm` and nothing else. |
| jump | `restrict,port-forwarding,permitopen="192.168.64.2:22",…,permitopen="192.168.64.254:22",command="/usr/bin/false"` | Forward to a VM's ssh port and nothing else. |

Two things about the jump line that are easy to get wrong:

- **One `permitopen` per address.** OpenSSH matches the host of `permitopen` literally, with no wildcard, so
  `192.168.64.*:22` forwards nothing. The script lists .2 to .254 (about 7.5 KB). The Mac itself is .1 and stays out of
  reach. If Tart's network on your Mac is not 192.168.64.x, run with `UX_VM_NET` set to its first three numbers.
- **The forced `command`.** `restrict` alone still lets a key run commands on the Mac. The forced `/usr/bin/false` stops
  that and leaves forwarding (which `ProxyJump` uses) working.

**The checks** run when Remote Login is on. With `--from`, they run first on lines limited to 127.0.0.1 and the real lines
replace them afterwards, so the lines that get used have been checked. They confirm that the control key runs `ux-vm` and
nothing else; that the jump key runs no command; that a forward to the Mac (127.0.0.1 and 192.168.64.1) is answered
`administratively prohibited`; and that a forward to 192.168.64.2 is not, which needs no VM. After the first
`ux-vm reset`, `ssh <that IP>` through the jump key is the real test of the allowed case. The checks were designed and
exercised against OpenSSH 9.2 on Linux and a stand-in `ssh`; that Apple's sshd matches `permitopen` the same way is to
be confirmed on the first run on the Mac.

**The keys as fleet secrets.** The private keys stay in files. `control.b64` and `jump.b64` hold them base64-encoded on one
line, because a multi-line value does not survive `fleet.env`. Paste each into /fleet as `UX_SSH_CONTROL_KEY` and
`UX_SSH_JUMP_KEY`. The bot decodes them with `base64 -d` into `~/.ssh` with mode 600, and uses the ssh config the script
prints.
