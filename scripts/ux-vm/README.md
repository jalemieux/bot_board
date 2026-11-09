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

## build-base

```sh
scripts/ux-vm/build-base            # both images
scripts/ux-vm/build-base linux      # one
scripts/ux-vm/build-base mac --pubkey ~/.ssh/ux_jump.pub --force
```

Builds `ux-base-linux` (from the Cirrus Labs Ubuntu image) and `ux-base-mac` (from their macOS Sequoia image). Each gets
the bot's jump key on the `admin` user, Node, Playwright and Chromium, and nothing from bot_board: installing bot_board
is what the journeys test. An image that exists is left alone unless `--force`. An image is built under a temporary name (`ux-base-<os>-building`) and renamed at the end, so a failed build leaves nothing for the next run to take as finished; a failed `--force` build keeps the old image. The key goes in over stdin with `tart exec -i`, and the build checks that it landed. The first build downloads several GB.

## What was tested

`tests/test_ux_vm.py` runs `ux-vm` against a stand-in for `tart`: the action parsing and refusals, reset, a second
reset starting from a fresh clone, stop, status, a missing base image, a VM that dies or never answers, and `build-base`:
building under a temporary name and renaming, a failed build leaving no image, the key check, a failed `--force` build keeping the old image, and existing images left alone. It does **not** run Tart. `build-base` and a real reset have not been run on a Mac, and
`build-base` assumes the Cirrus images' guest agent (`tart exec`) and their `admin` user.
