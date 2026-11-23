# Key files

Key files are the files that bots read as orders, that decide what merges, or that build what bots read
and hold their secrets. A change to one
reaches every bot at its next prompt, so it is reviewed more carefully than code.

- `AGENTS.md`, `ADMIN.md`, `SECURITY.md`
- `docs/skills/**`, `docs/profiles/**`, `docs/team/**`
- `.forgejo/workflows/`
- The code that builds what bots read or holds their secrets: `bin/bot`, `bin/keeper`,
  `app/fleet.py`, `deploy/forge-setup`

A pull request that changes one needs the human's yes in the thread as well as the two reviews.
The architect's verdict names the key files the change touches and the ones read for a
contradiction. The method is the `review-instructions` skill.

A merge does not deliver a change to a skill or a profile: the registry holds its own copy, and
a person has it updated. The review names that step.

To add or remove a file here, open a pull request. This list is itself a key file.
