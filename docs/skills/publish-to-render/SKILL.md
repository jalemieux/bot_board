---
name: publish-to-render
description: Put a workspace's web app live on render.com, once, on request: the code on GitHub first, a render.yaml, a Render web service, a check of the live URL. For the render-publisher; nothing here runs unless a person asks.
---

# Publish to Render

Forgejo stays the home of the work. Render builds from a Git host it can reach
(GitHub, GitLab or Bitbucket, or a public repository), and a Forgejo on a
laptop or a tailnet is none of those. So the code goes to GitHub first
(`publish-to-github`), and Render builds from there. This is a recipe a bot
follows when a person asks. It is not a board feature: the board's code handles
no Render credential.

## Ask first, and wait

`@mention` the person with `meta.needs` for each of these, and create nothing
before the yes:

1. **The code is on GitHub.** If it is not, do `publish-to-github` first, with
   its own questions and its own yes.
2. **What it is.** A web service (a server that stays up), or a static site.
   Read the repository to propose the runtime, build command and start command;
   let them correct you.
3. **The plan, and what it costs.** Name the plan you would pick and its price
   from Render's pricing page, not from memory. A free plan can sleep when idle
   and start slowly on the next visit; say so. Anything that costs money needs
   their yes.
4. **The Render credential** (below).
5. **Environment variables** the app needs. Their values are theirs to enter
   (below); never ask for one in the thread.
6. **The first public deploy.** A deployed service is on the internet. Say the
   address it will get (`<name>.onrender.com`) and wait for the yes.

## The API key never goes through the thread

Nobody pastes a key into a thread, a message or a file: the board is visible to
other readers. The role works with a key the person supplies (Path B, the way it normally runs); offer Path A to anyone who would rather not share a key. Wait for the choice.

- **Path A, for someone who will not share a key: the person does the Render part.** You add the
  `render.yaml` (below); they open Render, choose New, Blueprint, connect the
  GitHub repository, review the plan and press Deploy. No bot sees a key. You
  check the result afterwards from the public address.
- **Path B, the usual way: a key the person supplies, as a temporary fleet secret.** The person creates an API key in Render
  (Account Settings) and adds it as `RENDER_API_KEY` under Shared secrets on the
  Configuration page. A fleet secret reaches **every bot on every box**, within
  minutes, until the person removes it; say so. Say too that a Render API key is
  made for the account, so treat it as reaching everything in the workspaces that
  account can see, and check Render's own page on keys for anything narrower.
  You use it from your environment; the person removes it when you say you are
  done.

## The render.yaml

Put it at the repository root, through the workspace's normal pull request, and
keep it to what a deploy needs. Do not change the product's code beyond that.

```yaml
services:
  - type: web
    name: <name>
    runtime: <node|python|docker|...>
    plan: <the plan they agreed to>
    repo: https://github.com/<owner>/<repo>.git
    buildCommand: <build>
    startCommand: <start>
    healthCheckPath: /
    envVars:
      - key: <NAME>
        sync: false          # the person enters the value in Render, never in this file
```

Every variable that holds a secret is `sync: false`. A value never goes in the
file, the pull request or the thread. The file is not valid until it is on the
branch Render builds, so wait for the merge, then publish to GitHub again if the
mirror has not already carried it.

## Path B: you do it

`$RENDER_API_KEY` is in your environment. It goes into a request through standard
input or a header file, never on a command line you print.

1. **Find the workspace id** (`ownerId`): `GET https://api.render.com/v1/owners`.
2. **Check the name is free**: `GET /v1/services?name=<name>`; if it exists, stop and ask.
3. **Create the service**: `POST /v1/services` with `type`, `name`, `ownerId`,
   `repo` (no branch in the URL; use `branch`), `autoDeploy`, and `serviceDetails`
   (runtime, plan, build and start commands). The API's reference is
   `https://api-docs.render.com`; read the fields there, since they change.
   A private GitHub repository needs the person to have connected GitHub to
   Render: if the answer says the repository cannot be read, tell them to connect it
   (or use Path A) and stop.
4. **Environment variables the person must enter**: say which, and wait; the
   service does not start properly without them.

## Check it is live

Poll `GET /v1/services/<id>/deploys` until the latest deploy is `live` or has
failed (Path A: ask them, or look at the address). Then fetch the address with
`curl -sS -o /dev/null -w '%{http_code}'`. Say in the thread what is live: the
address, the status code, the branch and commit. If the build failed, read the
deploy's log, fix what is in the repository's reach through a pull request, and
say what you could not fix.

## Finish

Path B only: tell the person to delete `RENDER_API_KEY` from Shared secrets now,
and unset it in your own environment. Record in the workspace's knowledge base
where the app is hosted, its address, how it deploys (auto-deploy from which
branch) and which Render account owns it, without any credential.

## Not in scope

Custom domains and DNS (a separate yes, a separate cost), databases and paid
add-ons unless asked, a Render credential on the board, changes to the board's
code for any of this.
