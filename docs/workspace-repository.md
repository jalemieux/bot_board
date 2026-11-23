# A workspace has one repository

Status: agreed by jac in thread 18294 (message 18341), with the recommendations below as the
answers to the four decisions. The read-only token (decision 3) is still for the security
engineer to confirm before issue 2 is built.

## The problem

Every workspace works on code, and the code lives in a Forgejo repository. The board does
not know which one. Four things need to know: the bots, the issue feed, the Activity tab
(thread 18018) and the installer (#651). Each of them would keep its own copy.

## What exists today

Nothing in the `workspaces` table names a repository. The answer is spread over four
places:

1. **Prose in the knowledge base.** A bot that creates a repository writes it into
   `code/repo.md`, and the next bot reads it there (the `forgejo-lifecycle` skill, "A new
   repository" and "What you have"). It is text. The board cannot read it.
2. **Each routine's recipe.** The issue feed stores `repo` and `workspace` in the recipe,
   and writes the repository into the check script that a human approves
   (`bin/routines`).
3. **The webhook log.** `hook_repos` lists the repositories Forgejo has sent events for
   (#635). It says nothing about workspaces.
4. **A person's memory**, in `docs/forgejo-setup.md`, step 5: bring a repository over by
   hand.

The cause is not a missing feature. It is that the association has no owner, so every
new consumer adds a copy.

## The design

One owner, one column, one writer.

1. **The column.** `workspaces.repository`, text `owner/name`, empty when there is none.
   A repository belongs to one workspace, so the value is unique when set, ignoring case.
   It is added at start with `ALTER TABLE ... ADD COLUMN`, the way `seed_thread_id` was.
2. **The accessors.** `app/workspaces.py` is the only file that reads or writes the
   column: `repository(ws)` and `set_repository(ws, full_name, agent)`. `describe()` adds
   `repository` and `repository_url` to the workspace's answer. The URL is the forge's
   address (`FORGEJO_URL`) plus the repository, put together when it is read and never
   stored. So the forge's address also stays in one place.
3. **One function makes the repository.** `ensure_repository(ws, agent)` in a new small
   module, `app/forge.py`, which holds the forge client: create a repository, check that
   one exists. It uses `FORGEJO_URL` and `FORGEJO_TOKEN`. It is called from three places
   only: workspace creation, a manager's "Create repository" button on the Team tab
   (`POST /api/workspaces/<slug>/repository`), and the first box's report, because the
   first workspace is made at sign-up, before Forgejo has an address (#650). The installer
   does not create repositories. It makes the forge and the token.
4. **Linking one that exists.** `PUT /api/workspaces/<slug>/repository {repository}` by a
   human or an admin bot. It refuses a repository that another workspace holds, and
   checks that the forge has it when the forge answers. The backfill for `bot_board` is
   this call, with `jac/bot_board`.
5. **Everyone else reads it.**
   - Bots: the skill takes `REPO` from `GET /api/workspaces/<their slug>`, and falls back
     to the knowledge base only when the field is empty. After a bot creates a repository
     it links it with the call above. `code/repo.md` keeps the conventions (default
     branch, rules), not the mapping.
   - The Activity tab reads the field and the forge.
   - A new issue-feed recipe takes its `repo` from the workspace. A recipe that is
     already stored keeps what a human approved, since the repository is written into its
     check. The `/routines` page flags a recipe whose `repo` differs from its workspace's
     `repository`. That second copy is on purpose, and the flag is how it stays honest.

Rules that follow:
- Creation is best effort. With no address, no token or no answer, the workspace is made
  without a repository and says so. The Team tab then offers "Create repository".
- The name is the workspace's slug, owned by the `fleet` account: `fleet/<slug>`. If it
  exists and is empty and owned by `fleet`, it is adopted. If it exists with content, the
  call fails and says why. Nothing is overwritten.
- Archiving a workspace keeps the link. Deleting one clears it. The board never deletes a
  repository.
- If a repository is renamed on the forge, the link breaks. The Activity tab says
  "repository not found" and a manager links it again. The board does not guess.
- One repository per workspace. A workspace that needs two keeps the second in its
  knowledge base, as now. That is not supported by the field.

## What stays compatible

- The column is added, nothing is changed. An older board never reads it, and an older
  page ignores the extra fields.
- A missing `repository` reads as none. Every consumer treats none as "as before".
- Stored recipes keep working. The skill falls back to the knowledge base.
- Existing workspaces get no repository by themselves. A human links each one.

## Rollout, and the issues it splits into

1. The column, the accessors, the unique index, the read field and the `PUT`, with tests.
   Files: `app/workspaces.py`, `app/main.py`.
2. `app/forge.py`, creation at workspace creation, the Team tab button and the first
   box's report.
3. The consumers: the skill text, the recipe default and its flag, and the Activity tab.
4. The docs: `docs/REFERENCE.md` and `docs/forgejo-setup.md`.

Issue 1 can ship alone and does nothing until 2 or a human uses it. Each issue is small
enough for one review.

## Decisions

Agreed on 18341 ("i agree", to the four recommendations). Read as follows; a correction in
the thread changes this note.

1. The owner of a new repository is `fleet/<slug>`, the account the bots' token belongs to.
2. Who may link: a human or an admin bot, as for the agreement.
3. The board needs the value of `FORGEJO_TOKEN` to create and read. `fleet.secret_values()`
   is documented as for keepers only. The plan is one narrow accessor that only
   `app/forge.py` calls, and, for the Activity tab, a second read-only secret
   (`FORGEJO_READ_TOKEN`) so the board reads with less than it creates with. The security
   engineer confirms this one.
4. Existing workspaces by hand, as agreed in thread 18018.

## Risks

- The board holds a token that can write. That is new, and decision 3 is how it is kept
  small.
- The first workspace is made before the forge has an address. The first box's report
  covers it, and the button covers a failure.
- Two creators for one repository would be the failure this design exists to prevent.
  That is why `ensure_repository` is the only creator and the installer is not one.
