"""Routines for the board page: the definitions bin/routines runs, read from #admin.

The runner stays the authority on whether a routine runs. This module reads the
admin threads with the runner's own code (bin/routines, loaded as a module), so
the page shows the states the runner acts on, and it writes a definition exactly
as `routines add` does, so the runner accepts what a browser posted. Enable,
pause and test are plain replies in the thread; nothing here switches anything.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os

from . import db

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "routines")


def _load():
    loader = importlib.machinery.SourceFileLoader("bot_board_routines", _PATH)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


cli = _load()
ADMIN = cli.ADMIN
DEFAULTS = cli.DEFAULTS
JOBS_SHOWN = 5


def _all(**kw) -> list[dict]:
    out, since = [], 0
    while True:
        rows = db.list_messages(since=since, limit=500, **kw)
        out += rows
        if len(rows) < 500:
            return out
        since = rows[-1]["id"]


def _jobs(r: dict) -> list[dict]:
    """The latest messages the runner fired for this routine, and how each went."""
    rows = db.conn().execute(
        db._MSG_SELECT + " WHERE b.slug=? AND m.reply_to IS NULL AND json_extract(m.meta,'$.routine')=?"
        " ORDER BY m.id DESC LIMIT ?", (r["channel"], r["thread"], JOBS_SHOWN)).fetchall()
    out = []
    for m in db._hydrate(rows):
        replies = db.list_messages(thread=m["id"], since=m["id"], limit=500)
        done = next((x for x in replies if x["meta"].get("done") is True
                     and x["author"] != cli.ME and x["author_kind"] != "human"), None)
        out.append({"id": m["id"], "channel": m["channel"], "created_at": m["created_at"],
                    "sends": 1 + sum(1 for x in replies if x["author"] == cli.ME and "resend" in x["meta"]),
                    "done_by": done and done["author"], "done_at": done and done["created_at"]})
    return out


def list_routines() -> list[dict]:
    admin = _all(board=ADMIN)
    by_thread: dict[int, list[dict]] = {}
    for m in admin:
        by_thread.setdefault(m["thread_id"] or m["id"], []).append(m)
    out = []
    for r in cli.routines_of(admin):
        msgs = by_thread[r["thread"]]
        failed = next((m for m in reversed(msgs) if m["author"] == cli.ME and m["meta"].get("routine_failed")), None)
        test = next((m for m in reversed(msgs) if isinstance(m["meta"].get("routine_test"), dict)), None)
        out.append({**r,
                    "failure": failed["body"] if failed and r["state"] == "failed" else None,
                    "last_test": test and {**test["meta"]["routine_test"], "id": test["id"], "at": test["created_at"]},
                    "jobs": [] if r["state"] == "invalid" else _jobs(r)})
    return out


def create(data: dict, agent: dict, max_body: int) -> dict:
    """Post a definition (or a new version of one, with `thread`) as `routines add` would."""
    text = lambda k: (data.get(k) or "").replace("\r\n", "\n")
    r = {"name": text("name").strip(), "channel": text("channel").strip().lower(), "check": text("check"),
         "prompt": text("prompt").strip(), **{k: data.get(k, v) for k, v in DEFAULTS.items()}}
    for k in ("about", "skill"):
        if text(k).strip():
            r[k] = text(k).strip()
    thread = data.get("thread")
    if thread is not None:
        root = db.get_message(thread)
        if root is None or root["channel"] != ADMIN or root["reply_to"] is not None \
                or not isinstance(root["meta"].get("routine"), dict):
            raise KeyError(f"no routine in #{ADMIN} thread {thread}")
    body = cli.body_of(r, thread is not None)
    problem = cli.problem_with(r, body, thread is not None)
    if problem:
        raise ValueError(f"not posted: {problem}")
    if len(body) > max_body:
        raise ValueError(f"not posted: the routine's message is {len(body)} characters, over the board's {max_body}")
    if thread is None:
        db.ensure_board(ADMIN, "Fleet administration: one thread per routine (recurring work); a human replies "
                        "enable <code> or pause in a routine's thread. Not a project.", agent["id"])
    m = db.post_message(ADMIN, agent["id"], body, thread, [], {"routine": r})
    return {"message": m, "thread": m["thread_id"] or m["id"], "code": cli.code_of(r)}
