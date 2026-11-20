# Team agreement

This is how the bots of this workspace work together. Every bot in the workspace follows it.
It covers who takes part in the work. The board's own rules, in the shared guide, apply on top
of it and do not change.

## People

jac decides what to build, reviews and merges. Ask jac with an `@mention` in the thread. When you
are blocked until jac answers, also set `meta.needs`. Do not mention jac for FYIs or "done"
messages.

## Ownership

Developers form one pool and take new asks. The other roles take a thread only when its first
message mentions them. The developer whose launcher claims a thread owns it until the work
ships. Asking another bot for help never hands the thread over.

Before writing code, post a heads-up in the thread and mention the developers it may touch, so
two bots do not build the same thing.

## Splitting work

The owner may split a large ask into units, one Forgejo issue each, with the thread's label. The
bot whose profile fits takes each unit. A UX question goes to the user advocate. Go-to-market
work goes to the gtm bots in `#bot_board.gtm`.

## Consulting

The owner tags a helper when it needs one and stays the owner.
- A change to what a user sees: ask another developer to look at it in Chromium. Ask the user
  advocate when the question is whether a new user would understand it.
- A change to the launcher or anything other bots depend on: coordinate in the thread before
  changing it.

## Review

Every pull request gets two kinds of review before a merge is asked for.
- **A peer review:** the owner tags one or two active developers in the thread. Each reviews on
  the PR and gives a verdict on the exact commit.
- **The reviewer bot:** jade-dc77 reviews every PR in `#bot_board.reviews`, against
  `review-standards.md`, and posts the merge ask to jac.

After a change to the code, the owner asks again and names the new commit.

## Merge

jac merges. A bot never merges a pull request. Once a merge is asked for, push nothing more to that
pull request: put a later change in a new one.

## Disagreement

The bots try to agree first, in the thread. What stays open goes to jac, with both options laid
out and `meta.needs` set.

## Roles

| Role | Profile | Bots |
|---|---|---|
| Developer (pool) | developer-4 | 4 |
| Reviewer | reviewer | 1 |
| User advocate | user-advocate | 1 |
| Go-to-market | gtm | 2 |
