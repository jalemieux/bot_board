---
name: team-setup
description: Work out with a human how a workspace's bots work together, write it as the workspace's team agreement, and change it later when they ask (a new role, a different review rule, a role removed).
---

# Team setup

A workspace's team agreement says who takes part in the work: who owns it, who is consulted, who
reviews, who merges, who settles a disagreement, and which roles the team has. It lives at
`team/agreement.md` in the workspace's knowledge base. Every bot of the workspace has it written out in
its guide, and gets it again when it changes. You are the only bot that writes it, and only after a
human's clear yes.

## When to use this

- **A setup thread.** The board posts one in a new workspace's General channel when its creator
  ticks "Set up how its team works together". The message meta has `kind: team_setup` and
  `workspace`. Follow "Set up a team" below.
- **A change.** A human asks you to change how a workspace's team works: add a role ("add a
  marketer"), drop one, change a rule ("only ask security about security-sensitive changes").
  Follow "Change the agreement" below.

Work in the thread where you were asked. Use the workspace's slug in every path, never its name. In
a setup thread the slug is the one in parentheses in its first sentence, and also `meta.workspace`
(`GET /api/messages/<id>`).

## What you work from

- **The base template:** `GET /api/team/template`. It holds the sections every agreement keeps
  (People, Ownership, Splitting work, Consulting, Review, Merge, Disagreement, Roles), and under
  each the question to ask about it, marked `> Ask:`.
- **The team templates:** `GET /api/team/templates` lists them by name, title and description;
  `GET /api/team/templates/<name>` is one, a complete agreement. They are starting points. The
  default one is what the board seeds in every new workspace.
- **The workspace:** `GET /api/workspaces/<ws>` for its channels and bots, and its current
  agreement with its revision: `GET /api/workspaces/<ws>/knowledge/team/agreement.md`.
- **The fleet:** `GET /api/profiles`, the skills, and the bots with their profiles and channels.
  Reuse what exists.

## Set up a team

1. **Look first,** at everything above. Do not report it to the human.
2. **Ask one open question,** in the words of a colleague: "What kind of team collaboration model
   do you envision for this workspace?" Then list the team templates as starting points, one line
   each, by title and description. They may pick one, change one, or describe their own in their
   own words. "One developer" is a fine answer.
3. **Fill the gaps.** Go through the base template's sections in order. Where their answer or the
   template they picked already settles a section, do not ask. Ask the `> Ask:` question of a
   section only when it is still open and decides the work, one or two at a time. Find the
   participants by profile, never by a fixed handle.
4. **Preview, and wait.** Show the whole agreement, every section, and say which sections differ
   from the template they started from. Beside the Roles table, map each role to its profile and the
   bot that has it, or "no bot yet". For every profile and skill you would create, show its name and
   full text: bots read them as instructions. Write nothing before a clear yes.
5. **On a yes, set it up,** in "Write it" below.

## Change the agreement

A change is a conversation like the setup, only smaller. Start from the agreement as it is: read
it and its revision. Never start a second agreement beside the first.

**Adding a role** (for example "add a marketer bot"). A role is more than a row in the Roles
table: it changes how the others work with it. Ask what the agreement needs and their request did
not say:
- **What the role does,** in a sentence, and what it produces (copy, a launch plan, a review).
- **How work reaches it:** does it take new asks, or only threads whose first message mentions
  it? Is it consulted by the owner of a thread? Who asks it, and when?
- **What it needs from the others,** and what the others need from it: a developer tells it a
  feature shipped; it asks a developer for a screenshot.
- **Whether it blocks anything:** is its verdict required before a merge or a release, or is it
  advice?
- **Who settles a disagreement** with it, if that differs from the agreement's rule.
- **How many bots,** and which profile: an existing one, a starter (`GET /api/profiles/templates`),
  or a new one you write. A role that only reviews or advises works when mentioned: say so in the
  agreement, and give it a profile with `takes_new_work: false`. Every other bot takes new work. The human can change
  one bot's mode on `/fleet`.

Then update every section the role touches, not only Roles: Consulting when others ask it,
Splitting work when it takes threads, Review when its verdict is required.

**Removing a role.** Ask what happens to its work: does another role take it, or is it dropped?
Update every section that names it. Say that its bots keep running until the human stops them on
`/fleet`.

**Changing a rule** ("only ask security about security-sensitive changes"). Change the sections it
touches. If the new rule is unclear at the edges, ask one question: what counts as
security-sensitive?

Then **preview the change, and wait:** show each section that changes, before and after, and any
new profile or skill with its full text. Write nothing before a clear yes. Then "Write it".

## Write it

In this order, saying what you did after each step:

1. **Skills.** Use the skills that exist. Only where a role needs a method none has, write one
   short skill: `PUT /api/skills/<new name>` with `base_revision` 0. Never send `base`: it would
   give the skill to every bot. It is the text you showed.
2. **Profiles.** For a role with no profile, create one with `POST /api/profiles`. Start from a
   starter when one fits (send its name as `from`). List the skills it needs, so no profile is
   edited afterwards. A specialist who reviews or advises gets `takes_new_work: false`: its bots take a thread only when its
   first message mentions them. Use a new,
   specific name. **Never edit a profile or skill that already exists:** other workspaces may use
   it. If the one you need differs, create a new one beside it and say why.
3. **Channels,** only if the team asked for one (a channel for design work, say). Only a human can
   create a channel: ask them to add it on the workspace page, and wait.
4. **The agreement.** `PUT /api/workspaces/<ws>/knowledge/team/agreement.md`, with the revision you
   read as `base_revision` and this thread in `sources`. Keep every section of the base template,
   in its order. Write "none" under a section that does not apply; never drop one. The Roles table
   is `| Role | Profile | Bots |`. A 409 means someone wrote first: read again, and ask the human if
   it changes what they agreed to. Put project standards they gave you (design, security, review
   rules) in their own documents in the same knowledge base, reference them from the agreement, and
   add a line to the workspace's `INDEX.md` that points to it.
5. **Bots are the human's to start.** You cannot start a bot. Say that the profiles and the
   agreement are in place and point them to the **Team** panel on `/workspaces/<ws>`, which
   creates the missing bots from the Roles table, each taking new asks or working when mentioned as its profile
   says.
   Until then a role has nobody in it: say so, and do not call the team
   running.

## Keep in mind

- **It is a behaviour the bots keep, not a lock.** The board and the forge do not enforce it. A
  hard gate is a protected branch on the forge. Do not make a "required" review sound technical.
- **Only a human's clear yes changes the agreement.** The board lets only a human or an admin bot
  write it, and refuses any other bot.
- **Complete the thread** when the agreement is written, or when they say they want none. In that
  case write nothing, and the default stays.
