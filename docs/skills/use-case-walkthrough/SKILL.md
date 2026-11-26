---
name: use-case-walkthrough
description: Use a product the way its user would and come back with a verdict. Read and keep the use cases document, bring the real product up, walk one use case from a clean state, and get each finding to the bot who can fix it.
---

# Use case walkthrough

A product is good to use when a person can do what they came to do, without
guessing and without help. Nobody on the team sees that from the code. You see
it by being that person: you open the product, try the thing, and write down
what happened. Your job is to come back with a verdict the team can act on.

## 1. The use cases document

`docs/use-cases.md` in the repository lists what people come to the product to
do. Read it before every walkthrough. Do not invent the use cases each time: two
bots that do so test two different products.

It is one page, with one entry for each use case:

```
### Set up a first team
Who: a developer who has never used the product.
Goal: two bots answering in a channel.
Start: a clean machine and the install page.
Success: a message posted in the channel gets an answer from a bot.
Last verdict: works with friction, 2026-11-25, thread 123.
```

Where the repository has a product and brand document, take "who" from its
audience. Link to it. Do not copy it.

**When the repository has none, do not hold a walkthrough for it.** Walk the use
case the thread names, and open one issue to draft the document.

**To draft it,** read the public docs and pages, and use the product. List what
it lets a person do, from the most common to the least. For each, write the
entry above. Where you cannot tell who a use case is for, or whether the product
means to support it, ask the human: do not choose for them. Open it as a pull
request and get a human's agreement.

**Keep it true.**

- Every entry has a success a person could see on the screen.
- Every verdict carries its date and its thread.
- When the product gains a use, loses one or changes one, update the document.

## 2. Bring the product up

Use the real thing, the way its user gets it.

- **A web app.** Open it in a real browser. Use a fresh profile with no saved
  logins.
- **A game.** Run the exported build where there is one. Otherwise run the
  project in the engine, such as Godot, and play from the first screen.
- **A command line tool.** Install it the way the docs say, in a terminal.
- **An install.** Use a clean machine. Where the workspace provides one, such
  as a fresh VM, start every run from it.

Look first in the channel's focus and the knowledge base for how this workspace
runs its product for testing. Follow that where it exists.

If you cannot bring it up, stop. Say in the thread what you tried and what
failed, and ask a developer for what you need. A product you could not start is
itself a finding when a user would have hit the same wall. Do not read the code
and guess what the screen would show.

## 3. Walk the use case

One use case per thread.

1. Start from the entry's starting point, in a clean state.
2. Know only what the user knows: the public docs and what the screen shows.
   Leave aside the repository, the internals and earlier threads.
3. At each step, write three things: what you did, what you expected, and what
   happened.
4. Take a screenshot wherever a problem can be seen.
5. Note the time when you start and when you reach success, or give up.
6. When you get stuck, do what a user would do: read the screen again, then
   the docs. If that does not get you through, the use case has failed. You may
   go on with a workaround to find what comes after, and say that you did.

Each of these is a finding:

- a step where you had to guess
- a step the docs describe differently from what happens
- an error that does not say what went wrong or what to do next
- something you had to fix by hand
- a wait with no sign that anything is happening
- a screen where the next action is hard to find
- text, art or sound that is hard to read, see or hear

## 4. The verdict

Post one message in the thread:

```
Use case: Set up a first team
Verdict: works with friction
Time: 14 minutes, 9 steps
Stops the user: none
Slows the user: 1. The install page does not say which port to open. Issue #12.
Looks wrong: 2. The channel list overlaps the header on a narrow window. Issue #13.
```

- **Works.** A user reaches success with no finding that stops or slows them.
- **Works with friction.** A user reaches success, slowed down.
- **Fails.** A user does not reach success without help or a workaround.

Order the findings by what they cost the user, not by how easy the fix is.

## 5. Each finding to the bot who can fix it

File each finding as its own issue on the forge. Give it the steps to reproduce
it, what the user expected, what happened, how bad it is, and the screenshot.
Then bring in the bot who can change it. Mention it in the thread.

| The problem is in | Bring in |
|---|---|
| behaviour that is wrong or missing | a developer |
| the flow itself, or a fix that crosses components | the architect |
| layout, wording on the screen, a missing state | the UI designer |
| how the art looks, or how well it reads | the artist |
| public text that promises what the product does not do | the marketer |

These bots have no instructions for your question, so it carries everything
they need:

1. **Who the user is and what they were trying to do.**
2. **Where they got stuck:** the step, what they expected, what happened, the
   screenshot and the issue.
3. **What you need back:** a fix, options to choose from, or an answer on
   whether the product means to work this way.

```
@<ui designer> A first-time user is setting up a team. At step 4 they must
pick a workspace, and the button is below the fold on a laptop screen. I
scrolled past it twice. Screenshot and steps are in issue #13. Can you
propose where it should go?
```

Describe the problem, not the fix. When a bot proposes a fix or shows a mockup,
answer as the user you played: would this have got you through?

Where the team has none of these bots, the issue is enough.

## 6. Walk it again

When the thread shows a fix has merged, walk the whole use case again from a
clean state, not only the fixed step. Post the new verdict. Write it into the
document with its date.

## What not to do

- Do not give a verdict on a product you did not run.
- Do not use what a user could not know.
- Do not fix the code yourself.
- Do not call a use case working because a workaround got you through.
- Do not put several problems in one issue.
- Do not turn your own taste into a finding: say what it cost the user.
