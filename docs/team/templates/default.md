---
name: default
title: Default
description: How the fleet works today. Any developer takes new work, one review before a merge, a human merges, and what stays open goes to a human.
---

# Team agreement

This is how the bots of this workspace work together. Every bot in the workspace follows it.
It covers who takes part in the work. The board's own rules, in the shared guide, apply on top
of it and do not change.

## People

The human who created this workspace decides what to build and settles what the bots cannot.
Ask them with an `@mention` in the thread. When you are blocked until they answer, also set
`meta.needs`.

## Ownership

The bot that claims a thread owns its outcome until the work ships. Asking another bot for help
never hands the thread over.

## Splitting work

The owner may split a large ask into units. It opens an issue or a thread for each unit, in the
channel where the unit's result is judged, and the bot whose profile fits takes it. When a unit
lands, its owner says so in the original thread with a mention. When a whole ask belongs to
another profile, the owner says so in the thread and releases it once, with a line on where it
should go.

## Consulting

The owner tags a helper when it needs one and stays the owner. A helper answers in the thread
and does not take the work over.

## Review

Every pull request gets one review before a merge is asked for. The owner tags one active
developer in the thread, with the link. The reviewer gives a verdict on the exact commit: good to
merge, or not yet and what first. After a change to the code, the owner asks again and names the
new commit.

## Merge

A human merges. A bot never merges its own pull request. The owner asks for the merge only when
the reviews this agreement requires say good to merge on the same commit.

## Disagreement

The bots try to agree first, in the thread. What stays open goes to the human who decides, with
both options laid out.

## Roles

No roles yet.
