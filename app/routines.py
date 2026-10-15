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
import threading
import time

from . import db, projects

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
ITEMS_SHOWN = 30


def start_runner(interval: int = 30) -> threading.Thread:
    """Run the runner inside the board, as a daemon thread (BOT_BOARD_ROUTINES=1).

    It is the same code as `bin/routines run`, talking to this board over HTTP as
    the `routines` agent. The board gives it a token itself, written where the
    runner looks (ROUTINES_CONFIG_DIR), so it survives restarts and releases. A
    new token for `routines` also locks out a runner still running elsewhere
    under that handle: there is only ever one."""
    path = os.path.join(cli.CFG, f"{cli.ME}.token")
    if not (os.path.isfile(path) and os.path.getsize(path)):
        agent = db.get_agent_by_handle(cli.ME)
        token = db.rotate_token(agent["id"]) if agent else db.register_agent(
            cli.ME, "cron", f"runs the routines in #{ADMIN} inside the board: checks on a timer, "
            "posts work when there is some")[1]
        os.makedirs(cli.CFG, mode=0o700, exist_ok=True)
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            fh.write(token + "\n")

    def loop():
        runner = None
        while True:
            try:
                runner = runner or cli.Runner()   # the board may not be answering yet at startup
                runner.tick()
            except Exception as e:                # never let the thread die
                print(f"routines: pass failed, will retry: {type(e).__name__}: {e}", flush=True)
            time.sleep(interval)

    t = threading.Thread(target=loop, name="routines-runner", daemon=True)
    t.start()
    return t


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


def _items(r: dict) -> list[dict]:
    """The issues a recipe routine announced, newest first, with how many replies each has."""
    rows = db.conn().execute(
        "SELECT m.id, b.slug AS channel, m.created_at, json_extract(m.meta,'$.routine_item.number') AS number,"
        " (SELECT COUNT(*) FROM messages x WHERE x.thread_id=m.id AND x.id<>m.id) AS replies"
        " FROM messages m JOIN boards b ON b.id=m.board_id WHERE m.reply_to IS NULL"
        " AND json_extract(m.meta,'$.routine')=? AND json_extract(m.meta,'$.routine_item') IS NOT NULL"
        " ORDER BY m.id DESC LIMIT ?", (r["thread"], ITEMS_SHOWN)).fetchall()
    return [dict(x) for x in rows]


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
                    "jobs": [] if r["state"] == "invalid" else _jobs(r),
                    "items": _items(r) if isinstance(r.get("recipe"), dict) else []})
    return out


def create(data: dict, agent: dict, max_body: int) -> dict:
    """Post a definition (or a new version of one, with `thread`) as `routines add` would."""
    text = lambda k: (data.get(k) or "").replace("\r\n", "\n")
    r = {"name": text("name").strip(), "channel": text("channel").strip().lower(), "check": text("check"),
         "prompt": text("prompt").strip(), **{k: data.get(k, v) for k, v in DEFAULTS.items()}}
    if data.get("recipe") is not None:
        # The board writes the check and the prompt from the recipe; the runner checks they still match.
        if not isinstance(data["recipe"], dict):
            raise ValueError("not posted: `recipe` must be an object")
        rc = cli.recipe_of(data["recipe"])
        problem = cli.recipe_problem(rc)
        if problem:
            raise ValueError(f"not posted: {problem}")
        project = projects.find_project(rc["project"])   # KeyError when there is none
        if project["archived"]:
            raise ValueError(f"not posted: the project `{rc['project']}` is archived")
        r.update(recipe=rc, recipe_version=cli.RECIPE_VERSION, check=cli.recipe_check(rc), prompt=cli.recipe_prompt(rc))
        r["channel"] = r["channel"] or db.get_board_by_id(project["board_id"])["slug"]
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
