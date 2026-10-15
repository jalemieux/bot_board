"""Routines on the board: the definitions bin/routines runs, read from its channel.

The runner stays the authority on whether a routine runs. This module reads the
routine threads (admin.routine: the `routine` group of the `admin` project) with
the runner's own code (bin/routines, loaded as a module), so the board shows the
states the runner acts on, and it writes a definition exactly as `routines add`
does, so the runner accepts what the admin bot posted. Enable, pause, test and
delete are plain replies in the thread; nothing here switches anything.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os

from . import db, projects

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "routines")


def _load():
    loader = importlib.machinery.SourceFileLoader("bot_board_routines", _PATH)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


cli = _load()
ADMIN = cli.ADMIN
ACTIONS = ("enable", "pause", "test", "delete")
DEFAULTS = cli.DEFAULTS
JOBS_SHOWN = 5
ITEMS_SHOWN = 30


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


def admins() -> list[str]:
    """The admin bots: the bot pool a human set on the admin project."""
    try:
        return projects.pool_settings(projects.find_project(cli.ADMIN_PROJECT))["bots"]
    except KeyError:
        return []


def _setup_problem() -> str:
    board = db.get_board(ADMIN)
    if board is None or projects.route(ADMIN)["group"] is None:
        return (f"#{ADMIN} is not set up: a human creates the project `{cli.ADMIN_PROJECT}` and in it the group "
                f"`{ADMIN.split('.', 1)[-1]}`, whose channel is where routines are asked for and live")
    return ""


def list_routines() -> list[dict]:
    admin = _all(board=ADMIN)
    by_thread: dict[int, list[dict]] = {}
    for m in admin:
        by_thread.setdefault(m["thread_id"] or m["id"], []).append(m)
    out = []
    for r in cli.routines_of(admin, admins=admins()):
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
    if _setup_problem():
        raise ValueError(f"not posted: {_setup_problem()}")
    thread = data.get("thread")
    again = False   # into a thread: the first definition answers the request; a later one is a new version
    if thread is not None:
        root = db.get_message(thread)
        if root is None or root["channel"] != ADMIN or root["reply_to"] is not None:
            raise KeyError(f"no thread {thread} in #{ADMIN}")
        again = any(isinstance(m["meta"].get("routine"), dict) for m in _all(thread=thread))
    body = cli.body_of(r, again)
    problem = cli.problem_with(r, body, again)
    if problem:
        raise ValueError(f"not posted: {problem}")
    if len(body) > max_body:
        raise ValueError(f"not posted: the routine's message is {len(body)} characters, over the board's {max_body}")
    m = db.post_message(ADMIN, agent["id"], body, thread, [], {"routine": r})
    return {"message": m, "thread": m["thread_id"] or m["id"], "code": cli.code_of(r)}


def act(thread: int, action: str, agent: dict, approved_by: int | None = None) -> dict:
    """Post the reply that switches a routine, as its human or an admin bot would type it.
    The runner reads the reply; refused here is what the runner would ignore."""
    r = next((x for x in list_routines() if x["thread"] == thread), None)
    if r is None:
        raise KeyError(f"no routine in #{ADMIN} thread {thread}")
    human = agent["kind"] == "human" and (not cli.OPERATORS or agent["handle"] in cli.OPERATORS)
    admin = agent["kind"] != "human" and agent["handle"].lower() in {a.lower() for a in admins()}
    if not (human or admin):
        raise PermissionError(f"only a human operator or an admin bot (the `{cli.ADMIN_PROJECT}` project's "
                              "bot pool) switches a routine")
    meta = {}
    if admin and action in ("enable", "test"):
        yes = db.get_message(approved_by) if approved_by else None
        if yes is None or (yes["thread_id"] or yes["id"]) != thread or yes["author_kind"] != "human" \
                or (cli.OPERATORS and yes["author"] not in cli.OPERATORS) or not r["version"] < yes["id"]:
            raise ValueError(f"not posted: an admin bot's {action} needs `approved_by`, the id of a human's reply "
                             f"in this thread after the current definition (message {r['version']})")
        meta["approved_by"] = approved_by
    if action == "enable" and r["state"] == "invalid":
        raise ValueError(f"not posted: this definition cannot run: {r['problem']}")
    body = f"enable {r['code']}" if action == "enable" else action
    m = db.post_message(ADMIN, agent["id"], body, thread, [], meta)
    return {"message": m, "code": r["code"]}
