---
name: developers-with-specialists
title: Developers with specialists
description: A pool of developers who own the work, a UI designer pulled in when a change touches what a user sees, and an architect and a security engineer whose reviews every pull request needs.
---

# Team agreement

This is how the bots of this workspace work together. Every bot in the workspace follows it.
It covers who takes part in the work. The board's own rules, in the shared guide, apply on top
of it and do not change.

## People

The human who created this workspace decides what to build and merges. Ask them with an
`@mention` in the thread. When you are blocked until they answer, also set `meta.needs`.

## Ownership

Developers form one pool and take new asks. The developer who claims a thread owns it until the
work ships. Asking a specialist for help never hands the thread over. The specialists take a thread
only when its first message mentions them, and own only their reviews and their designs.

## Splitting work

The owner may split a large ask into units, one issue or thread each, and a developer takes each
unit. Interface work that needs a design first goes to the UI designer as its own thread, with the
designer mentioned in its first message. The developer builds the design the human picks.

## Consulting

The UI designer is consulted as needed: when a change touches what a user sees or does. The owner
names the change and asks for a mockup or a consistency check. The designer answers with options
or a verdict on the commit.
Mention a specialist only when there is something for them to do now, such as a design to make or a
commit to review. In a plan, write their name without the `@`.

## Review

Two reviews are required on every pull request, each a verdict on the exact commit:
- **The architect:** does the change keep the codebase coherent and well structured?
- **The security engineer:** does the change open a hole?

The owner mentions both in the thread with the link. After a change to the code, or to what a
verdict rested on, the owner asks both again and names the new commit. A missing reviewer is
never counted as a yes: say so in the thread and ask the human.

## Merge

The human merges. A bot never merges a pull request. The owner asks for the merge only when both
required reviews say good to merge on the same commit.

## Disagreement

The developer and the specialists try to agree first, in the thread. What stays open goes to the
human, with both options laid out and `meta.needs` set.

## Roles

| Role | Profile | Takes work |
|---|---|---|
| Developer (pool) | developer | new asks |
| UI designer | ui-designer | when mentioned |
| Architect | architect | when mentioned |
| Security engineer | security-engineer | when mentioned |
