---
name: publish-to-github
description: Publish a workspace's Forgejo repository to GitHub, once, on request: a GitHub repository, then a push mirror from Forgejo. For the publisher; nothing here runs unless a person asks.
---

# Publish to GitHub

Forgejo stays the home of the work. GitHub gets a copy, pushed from Forgejo, so
nothing needs the board to be reachable from the internet. This is a recipe a bot
follows when a person asks for it. It is not a board feature, and the board holds
no GitHub credential.

## Before you start

Ask the user in the thread, with an `@mention` and `meta.needs`, and wait. Ask
once, with all of it:
- the GitHub owner (their login or an organisation) and the repository name;
- public or private (suggest private);
- a GitHub token that can create the repository and push to it (a classic token
  with the `repo` scope, or a fine-grained one with Administration and Contents
  write). Tell them it is used once to set the mirror and is stored by Forgejo,
  not by the board.

Do not create anything before the yes. The token goes through an environment
variable, never into a file, a log, a command line you print or a message.

## Steps

`$FORGEJO_URL` and `$FORGEJO_TOKEN` are in your environment. The workspace's
repository is `repository` in `GET $BOARD/api/workspaces/<slug>`.

1. **Check GitHub has nothing there.** A push mirror makes GitHub match Forgejo:
   it deletes any branch Forgejo lacks. Only an empty or new repository is safe.
   ```bash
   gh repo view "$OWNER/$NAME" >/dev/null 2>&1 && echo "exists: stop and ask the user"
   ```
   If it exists and has content, stop and tell the user why. Do not continue.
2. **Create it, empty.**
   ```bash
   GH_TOKEN="$USER_TOKEN" gh repo create "$OWNER/$NAME" --private --disable-wiki
   ```
   Use `--public` only if the user said public.
3. **Set the push mirror on the Forgejo repository** (`<owner>/<name>` from the
   workspace). `sync_on_commit` pushes on every commit; the interval is a backstop.
   ```bash
   curl -sS -H "Authorization: token $FORGEJO_TOKEN" -H 'Content-Type: application/json' \
     "$FORGEJO_URL/api/v1/repos/<owner>/<name>/push_mirrors" \
     -d "$(jq -n --arg a "https://github.com/$OWNER/$NAME.git" --arg u "$OWNER" --arg p "$USER_TOKEN" \
          '{remote_address:$a, remote_username:$u, remote_password:$p, interval:"8h0m0s", sync_on_commit:true}')"
   ```
   The `fleet` account owns the repository when the board made it. If Forgejo
   answers 403, the account lacks admin on it: ask the user to add the mirror in
   the repository's Settings, Mirror settings, and give them the address.
4. **Check it worked.** Trigger a push, then look at GitHub.
   ```bash
   curl -sS -X POST -H "Authorization: token $FORGEJO_TOKEN" \
     "$FORGEJO_URL/api/v1/repos/<owner>/<name>/push_mirrors-sync"
   gh api "repos/$OWNER/$NAME/branches" --jq '.[].name'
   ```
   Say in the thread what is live: the GitHub address and the branches there.
5. **Forget the token.** Unset the variable. Record in the workspace's
   knowledge base that the repository is mirrored to `<owner>/<name>` on GitHub,
   without any credential.

## Issues from outsiders (optional)

GitHub issues do not reach a Forgejo on a laptop by themselves. If the user wants
outsiders to file issues, offer the issue bridge: `deploy/github-bridge/check`,
a routine that copies GitHub issues to Forgejo and carries comments both ways. It
is off by default and wakes no bot. Offer it; do not turn it on unasked.

## Not in scope

Exposing the board with a tunnel, a GitHub App, or storing the GitHub token on the
board. Changing the board's code for any of this.
