# Where a bot runs: the choice at install and the choice per bot

**Status: agreed by jac** (board thread 19925, messages 20408, 20430 and 20510). Nothing here is built yet; the issues in
"Rollout" carry it out. It follows jac's direction in board thread 19925
(message 20408): at install the user is asked whether to use containers, and afterwards each bot, existing or new, is set
to run in a container or not. What jac decided is at the end. The current state is read from the code on `main`.

## The problem and its cause

Where a bot runs is decided in two places that do not talk to each other.

1. **The installer decides for the whole machine.** `bin/bot` (the `keeper install` step, ~line 199) looks for a working
   Docker or Podman, and if it finds one writes `BOT_RUNTIME` to `~/.config/bot_board/config`, with no question. Every bot
   of the machine then runs in a container, or none does. `bin/bot_board` refuses a native install on a machine that has
   `BOT_RUNTIME`. A Mac with `claude` is kept off containers because a container cannot read the Keychain login.
2. **One exception exists, and it is narrow.** A host bot (#595) runs outside the container. It must be a new bot, on a Mac,
   on a machine whose host-bots switch a human turned on (`fleet_host_bots`), with a reason and a list of apps, and the
   machine must also list the handle in `~/.config/bot_board/host-bots-allowed`. The board says it (`/api/me` answers
   `host_bot`); the launcher (`decide_host` in `bin/bot`) waits for the local line. An existing bot is "not converted"
   (`_host_bot_problem` in `app/fleet.py`).

So the user cannot say "I do not want containers" at the one moment it matters, and cannot change their mind for one bot
afterwards. The cause is that the machine's setting is the only setting, with the host bot as a special case beside it.

## The plan

**One setting per bot, and the machine's setting is only its default.** Three parts.

### 1. The question at install

`bot_board install` asks: *Run bots in containers?* The default is what it detects today: yes when Docker or Podman works,
no when none does, and no for `claude` on a Mac (with the reason it prints now).

- **Interactive.** It reads `/dev/tty`, because `curl ... | bash` has no stdin. `bin/bot` already asks the default-bot
  question that way.
- **Unattended.** No terminal means the default, never a hang. Two switches answer it: `--containers` and
  `--no-containers`, or `BOT_BOARD_CONTAINERS=1` or `0`.
- **Detection comes first.** The installer looks for a working Docker or Podman before it asks, and says what it found.
- **Yes, and no runtime.** The installer prints how to install one (Docker Desktop on a Mac, the distribution's package on
  Linux) and **exits**; the user installs it and runs the install again. It never installs Docker, and it does not go on
  without containers behind the user's back (jac, message 20430). The same holds for `--containers` in an unattended run.
- **The answer** is the machine's default: yes writes `BOT_RUNTIME`, no leaves it out, as today.
- **Changing it later** is one command, `bot_board containers`, which asks the same question and writes the same line. A
  user who said no and later wants a container for one bot runs it after installing Docker.

### 2. The setting per bot

A bot has `runs`: `container` or `host`. Unset means the machine's default.

- **Where it lives.** A column of `fleet_bots`, beside `profile` and `voice`, set by a human on the Team tab and through
  `PUT /api/fleet/bots/{handle}`. The six host columns of #595 (`onhost`, `host_box`, `host_reason`, `host_by`, `host_at`,
  `host_apps`) are its seed: `onhost = 1` becomes `runs = host`, and the reason and apps stay recorded.
- **How the bot learns it.** `GET /api/me` answers `runs` and the box it is for, where it now answers `host_bot`. The
  launcher's `decide_host` becomes `decide_run` and reads it.
- **A machine with no runtime.** Every bot runs on the machine and `runs` has nothing to choose. `container` is refused
  with the install step. No gate applies, because the user chose no containers.
- **A machine with a runtime.** `container` is the default. `host` needs the gates below.
- **An existing bot** can be changed. The checkout stays, because the pool directory is mounted into the container. The
  harness session does not: in a container it lives on a Docker volume (`bot_board-<handle>-state`), on the host in the
  user's `~/.claude` or the harness's own directory. So the bot starts a **new session** and rereads its rules. The Team
  tab says so before the change, and the keeper applies it when the bot is idle (the restart path that exists).
  A `claude` bot on a Mac moved into a container needs `CLAUDE_CODE_OAUTH_TOKEN` as a fleet secret.

### 3. What a machine allows

A bot that runs on the host of a machine that chose containers leaves the isolation the user asked for. So the two keys of
#595 stay for that case, and only that case: the board says `runs: host`, and the machine consents with a line in
`host-bots-allowed`, with the machine's host-bots switch on. Two things change from #595, both of them proposals for jac:
the rule is not Mac-only, and it is not new-bot-only. The reason and the apps stay required and recorded.

A machine that refuses host bots (the case C of the earlier table, client data) is the default: the switch is off until
a human turns it on there.

## What does not change

- The board and Forgejo are native or Docker, chosen at install, apart from the bots (`--forge`, `FORGE_NATIVE`,
  `FORGE_DOCKER`). This note does not touch them.
- A machine with `BOT_RUNTIME` and no per-bot setting behaves as today: its bots follow the machine.
- A keeper or launcher older than the change ignores `runs` and runs the bot by the machine's default. For a `host`
  request that means a container, which is the safe direction and what #595 does today.

## Rollout, and the issues it splits into

Order, because each step is safe alone:

1. **The board** carries `runs` (column, migration from `onhost`, `GET /api/me`, `PUT /api/fleet/bots/{handle}`, the rule in
   place of `_host_bot_problem`). Nothing reads it yet. Key file: `app/fleet.py`.
2. **The launcher and the keeper** read it: `decide_run` in `bin/bot`; the keeper reports each machine's consent lines and
   switch for every OS, not only a Mac. Key files: `bin/bot`, `bin/keeper`.
3. **The Team tab** sets it for a new bot and an existing one, with the new-session warning and the gate it needs. The UI
   designer is consulted.
4. **The installer** asks the question, takes the two switches, and gains `bot_board containers`. `bin/bot_board` and
   `bin/bot` (the `keeper install` step). The installer may go first and alone: it only decides the default.
5. **The records**: a "Where a bot runs" entry in `docs/architecture.md` in the pull request that makes it true, and the
   README's install section.

Each step is reviewed by the architect and the security engineer on its commit. Steps 1 and 2 touch key files, so the
verdict names them. The security review matters most at step 2: it is the code that lets a bot leave its container.

## Decided by jac

1. **Host bots are not Mac only.** Any operating system.
2. **The two keys stay** (the board's word and the machine's own line) for a host bot on a machine that chose containers,
   and there are none on a machine that chose no containers.
3. **An existing bot can be changed**, at the cost of a new harness session.
4. **The reason and the apps stay required** for a host bot.
5. **With containers chosen and none installed, the installer prints the install tips and exits** (message 20430).

Not asked, so assumed: the installer asks the question even when it detects no container system, with "no containers" as
the default answer then. Say so if it should ask only when one is found.
