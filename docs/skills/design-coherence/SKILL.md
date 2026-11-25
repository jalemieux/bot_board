---
name: design-coherence
description: Keep a codebase to one way of doing each job. Read and keep the architecture document, test a design against it, and review a change for a second way of doing something the codebase already does.
---

# Design coherence

A codebase stays easy to change while each job is done one way: one way to run
background work, one event format, one way to store a setting. Each bot that
adds its own way leaves the next bot two to learn and two to fix. Your job is to
keep the count at one.

The design test and several of the principles here follow pstack by Lauren Tan
(MIT), fitted to the fleet.

## 1. The architecture document

`docs/architecture.md` in the repository says how the codebase is built. Read it
before every design note and every review. Do not work out the architecture
from the code each time: two bots that do so reach two answers.

It is one page, in three parts:

1. **Components.** Each part of the system and what it owns: its data, its files.
2. **Flows.** How data moves between the components, for the main paths.
3. **Patterns.** One entry for each recurring job, naming the one way it is done
   and a file that shows it:

```
### Background work
One way: a row in the `jobs` table, run by the worker loop.
Example: `app/jobs.py`, `enqueue()` and `run_next()`.
Not: a thread, a timer or a subprocess started from a request handler.
```

Jobs worth an entry: background and scheduled work, events and their message
format, storage and migrations, configuration and secrets, authentication,
errors and logging, calls to outside services, how a page gets its data.

**When the repository has none, do not hold a review for it.** Review the
change against the code you know, and open one issue to draft the document.

**To draft it,** read the entry points and trace the main flows. For each job above, count the ways the code does it today.
Where there is one, write it down. Where there are two or more, list them all
and ask the human which is the one: do not choose for them. Open it as a pull
request and get a human's agreement. If the repository keeps a list of key
files, that same pull request adds the document to it.

**Keep it true.** A stale document is worse than none.

- Every entry points at a real file, so anyone can check it.
- A pull request that adds or changes a pattern edits the document in the same
  pull request.
- When the code and the document disagree, one of them is wrong. Say which in
  the thread, and what should change.

The document owns how the code is built. A workspace's review standards own how
a review is written. When a finding would contradict either, say so in the
thread and let the human decide.

## 2. Designing

1. Trace every component the change touches, in the document and then in the code.
2. For each thing the change must do, look for its pattern in the document. Use
   it the way the example file does.
3. Where the design is yours to make, sketch two shapes: the data, the function
   signatures, which component owns what. Pick the one where each piece of
   state has one owner, each job has one way to do it, and callers need to know
   least.
4. Check the shape against these:
   - **Cause, not symptom.** Fix the first place the system went wrong. A guard
     where it surfaced hides the bug.
   - **Guards at the boundary.** Check data where it enters the system: a
     request, a file, another service. Inside, trust it. A second check of the
     same input at another trust boundary is not a duplicate: a permission
     checked again on the server, a value validated again where it reaches a
     shell, a path, a mention or the page, text escaped where it is shown.
     Leave those to the security review and do not ask for them to be removed.
   - **Safe to run twice.** A step that changes state reaches the same end
     state after a crash, a retry or a second run.
   - **Separate before locking.** When two actors write the same file, row or
     key, first ask whether each can have its own. Lock only when it must be
     shared.
   - **A structure, not scattered conditions.** A state the code checks in
     many places belongs in one data structure. Do not force it on code that
     is clear and local.
   - **Subtract first.** Remove what the change makes dead before adding to it.

## 3. A new pattern

Sometimes the listed way cannot do the job, or a better one exists. That is a
design decision, never a side effect of a feature.

- Write a design note: what the listed way cannot do, the new way, and what
  happens to the old one. Either every use is migrated, with the issues that do
  it, or the note says why both stay and which job each one is for.
- Get a human's agreement before any code uses the new way.
- The pull request that brings the new way in edits the document.

## 4. Reviewing

1. Read the document, then the change and the code it touches.
2. For each listed job the change does, compare it with the pattern's example
   file.
3. **A second way is a finding**, even when nothing breaks today. The case to
   give is the two paths side by side: where the codebase already does this job,
   and where the change does it differently. Say which should stay, and the
   smallest change that gets there. The verdict is not good to merge, unless
   the pull request carries the design note of section 3 and a human's
   agreement for the new way.
4. Look where a search stops: stored data, a message format, a field another
   version or another service reads.
5. **A pattern the document does not list is not a finding.** If you think the
   codebase has one, propose the entry in the thread, and enforce it once it is
   in the document.

## What not to do

- Do not let a second way in because it works.
- Do not enforce your own taste as a pattern.
- Do not record which of two existing ways is "the one" without a human's answer.
- Do not copy a rule from the document into another file. Link to it.
