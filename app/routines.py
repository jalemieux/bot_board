"""Routines: recurring work, stored on the board and run by bin/routines.

A routine is a row here, not a message: only the API changes it, and nobody has
to read machinery in a channel. What people do read stays in channels: the
conversation in which a human asks the admin bot for a routine (its `thread`),
the jobs the runner fires into project channels, and a heads-up when one fails.

Who may change one: a human who manages the admin project (or the board), or an
admin bot, a bot in that project's bot pool. An admin bot's enable and test need
`approved_by`, the id of such a human's reply in the routine's thread posted
after the current version was written, so nothing a bot wrote runs on the
runner's box before a human said yes to it. Every change to the definition is a
new version, paused again. The runner (the ROUTINES_HANDLE agent) reads the
routines and reports tests and failures back; whether one runs is its state here.

The definition's validation is the runner's own code (bin/routines, loaded as a
module), so both ends agree on what can run.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import threading
import time

from . import db, fleet, projects

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "routines")


def _load():
    loader = importlib.machinery.SourceFileLoader("bot_board_routines", _PATH)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


cli = _load()
DEFAULTS = cli.DEFAULTS
ADMIN_PROJECT = cli.setting("ROUTINES_PROJECT", "admin")
JOBS_SHOWN = 5
ITEMS_SHOWN = 30
FIELDS = ("name", "channel", "check", "prompt", "every", "claim_timeout", "done_timeout", "resends",
          "about", "skill", "recipe")

SCHEMA = """
CREATE TABLE IF NOT EXISTS routines (
    id INTEGER PRIMARY KEY,
    definition TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'paused',
    version INTEGER NOT NULL DEFAULT 1,
    defined_at INTEGER NOT NULL,
    thread_id INTEGER REFERENCES messages(id),
    approved_by INTEGER REFERENCES messages(id),
    failure TEXT,
    test_asked INTEGER NOT NULL DEFAULT 0,
    last_test TEXT,
    created_by INTEGER NOT NULL REFERENCES agents(id),
    updated_by INTEGER NOT NULL REFERENCES agents(id),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def start_runner(interval: int = 30) -> threading.Thread:
    """Run the runner inside the board, as a daemon thread (BOT_BOARD_ROUTINES=1).

    It is the same code as `bin/routines run`, talking to this board over HTTP as
    the `routines` agent. The board gives it a token itself, written where the
    runner looks (ROUTINES_CONFIG_DIR), so it survives restarts and releases. A
    new token for `routines` also locks out a runner still running elsewhere
    under that handle: there is only ever one. Checks get the fleet's secrets (GH_TOKEN,
    ...) as every box's bots do, read from the board at each run, so they are set once,
    on /fleet; they win over the container's own environment."""
    cli.secrets = fleet.secret_values
    path = os.path.join(cli.CFG, f"{cli.ME}.token")
    if not (os.path.isfile(path) and os.path.getsize(path)):
        agent = db.get_agent_by_handle(cli.ME)
        token = db.rotate_token(agent["id"]) if agent else db.register_agent(
            cli.ME, "cron", "runs the routines inside the board: checks on a timer, "
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


def init():
    with db._write_lock:
        db.conn().executescript(SCHEMA)


# ---------------------------------------------------------------- who

def admins() -> list[str]:
    """The admin bots: the bot pool a human set on the admin project."""
    try:
        return projects.pool_settings(projects.find_project(ADMIN_PROJECT))["bots"]
    except KeyError:
        return []


def is_operator(agent: dict | None) -> bool:
    """A human who manages the admin project, or the board when there is none."""
    if not agent or agent["kind"] != "human":
        return False
    try:
        return projects.can_manage(projects.find_project(ADMIN_PROJECT), agent)
    except KeyError:
        return projects.is_admin(agent)


def is_admin_bot(agent: dict) -> bool:
    return agent["kind"] != "human" and agent["handle"].lower() in {a.lower() for a in admins()}


def is_runner(agent: dict) -> bool:
    return agent["handle"] == cli.ME


def _require_switch(agent: dict) -> None:
    if not (is_operator(agent) or is_admin_bot(agent)):
        raise PermissionError(f"only a human who manages the `{ADMIN_PROJECT}` project, or an admin bot (its bot "
                              "pool), changes routines")


# ---------------------------------------------------------------- reading

def _row(rid: int) -> dict:
    row = db.conn().execute("SELECT * FROM routines WHERE id=? AND state<>'deleted'", (rid,)).fetchone()
    if row is None:
        raise KeyError(f"no routine {rid}")
    return dict(row)


def _jobs(r: dict) -> list[dict]:
    """The latest messages the runner fired for this routine, and how each went."""
    rows = db.conn().execute(
        db._MSG_SELECT + " WHERE b.slug=? AND m.reply_to IS NULL AND json_extract(m.meta,'$.routine')=?"
        " AND json_extract(m.meta,'$.routine_item') IS NULL ORDER BY m.id DESC LIMIT ?",
        (r["channel"], r["id"], JOBS_SHOWN)).fetchall()
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
        " ORDER BY m.id DESC LIMIT ?", (r["id"], ITEMS_SHOWN)).fetchall()
    return [dict(x) for x in rows]


def describe(row: dict, full: bool = True) -> dict:
    d = json.loads(row["definition"])
    test = json.loads(row["last_test"]) if row["last_test"] else None
    out = {**d, "id": row["id"], "state": row["state"], "version": row["version"], "thread": row["thread_id"],
           "approved_by": row["approved_by"], "failure": row["failure"] if row["state"] == "failed" else None,
           "test_pending": row["test_asked"] > (test or {}).get("for", 0), "test_asked": row["test_asked"],
           "last_test": test, "problem": cli.problem_with(d),
           "created_at": row["created_at"], "updated_at": row["updated_at"]}
    if full:
        out.update(jobs=_jobs(out), items=_items(out) if isinstance(d.get("recipe"), dict) else [])
    return out


def list_routines(full: bool = True) -> list[dict]:
    rows = db.conn().execute("SELECT * FROM routines WHERE state<>'deleted' ORDER BY id").fetchall()
    return [describe(dict(r), full) for r in rows]


def get(rid: int) -> dict:
    return describe(_row(rid))


# ---------------------------------------------------------------- writing

def _definition(data: dict, old: dict | None = None) -> dict:
    """A complete definition from what was sent (over `old`, for a change), or ValueError."""
    text = lambda v: v.replace("\r\n", "\n") if isinstance(v, str) else v
    sent = {k: text(data[k]) for k in FIELDS if data.get(k) is not None}
    r = {**DEFAULTS, **(old or {}), **sent}
    for k in ("name", "channel", "prompt", "about", "skill"):
        if isinstance(r.get(k), str):
            r[k] = r[k].strip()
    r["channel"] = (r.get("channel") or "").lower().lstrip("#")
    for k in ("about", "skill"):
        if not r.get(k):
            r.pop(k, None)
    if "recipe" in sent or (old and "recipe" in old and "check" not in sent and "prompt" not in sent):
        # The board writes the check and the prompt from the recipe; the runner checks they still match.
        if not isinstance(r["recipe"], dict):
            raise ValueError("`recipe` must be an object")
        rc = cli.recipe_of(r["recipe"])
        problem = cli.recipe_problem(rc)
        if problem:
            raise ValueError(problem)
        project = projects.find_project(rc["project"])   # KeyError when there is none
        if project["archived"]:
            raise ValueError(f"the project `{rc['project']}` is archived")
        r.update(recipe=rc, recipe_version=cli.RECIPE_VERSION, check=cli.recipe_check(rc), prompt=cli.recipe_prompt(rc))
        r["channel"] = r["channel"] or db.get_board_by_id(project["board_id"])["slug"]
    else:
        r.pop("recipe", None)
        r.pop("recipe_version", None)
    problem = cli.problem_with(r)
    if problem:
        raise ValueError(problem)
    return r


def create(data: dict, agent: dict) -> dict:
    """A new routine, paused. `thread`: the message where it was asked for, whose thread holds the yes."""
    _require_switch(agent)
    r = _definition(data)
    thread = data.get("thread")
    if thread is not None:
        m = db.get_message(thread)
        if m is None:
            raise KeyError(f"no message {thread}")
        thread = m["thread_id"] or m["id"]
    with db._write_lock, db.conn() as c:
        rid = c.execute("INSERT INTO routines(definition,defined_at,thread_id,created_by,updated_by,created_at,updated_at)"
                        " VALUES (?,?,?,?,?,?,?)", (json.dumps(r), db.cursor(), thread, agent["id"], agent["id"],
                                                  db.now(), db.now())).lastrowid
    return get(rid)


def update(rid: int, data: dict, agent: dict) -> dict:
    """A new version of the definition, paused until it is enabled again."""
    _require_switch(agent)
    row = _row(rid)
    r = _definition(data, json.loads(row["definition"]))
    with db._write_lock, db.conn() as c:
        c.execute("UPDATE routines SET definition=?, version=version+1, defined_at=?, state='paused', approved_by=NULL,"
                  " failure=NULL, updated_by=?, updated_at=? WHERE id=?",
                  (json.dumps(r), db.cursor(), agent["id"], db.now(), rid))
    return get(rid)


def _approval(row: dict, agent: dict, approved_by: int | None, action: str) -> int | None:
    """The human's yes an admin bot's enable or test rests on; a human operator needs none."""
    if is_operator(agent):
        return approved_by
    yes = db.get_message(approved_by) if approved_by else None
    author = yes and db.get_agent_by_handle(yes["author"])
    if yes is None or row["thread_id"] is None or (yes["thread_id"] or yes["id"]) != row["thread_id"] \
            or not is_operator(author) or yes["id"] <= row["defined_at"]:
        raise ValueError(f"an admin bot's {action} needs `approved_by`: the id of a reply from a human who manages "
                         f"the `{ADMIN_PROJECT}` project, in this routine's thread, posted after its current version")
    return approved_by


def act(rid: int, action: str, agent: dict, approved_by: int | None = None) -> dict:
    """enable, pause, test or delete."""
    _require_switch(agent)
    row = _row(rid)
    sets, args = [], []
    if action == "enable":
        problem = cli.problem_with(json.loads(row["definition"]))
        if problem:
            raise ValueError(f"this definition cannot run: {problem}")
        sets, args = ["state='enabled'", "failure=NULL", "approved_by=?"], [_approval(row, agent, approved_by, action)]
    elif action == "test":
        _approval(row, agent, approved_by, action)
        sets = ["test_asked=test_asked+1"]
    elif action == "pause":
        sets = ["state='paused'"]
    elif action == "delete":
        sets = ["state='deleted'"]
    else:
        raise ValueError(f"unknown action {action}")
    with db._write_lock, db.conn() as c:
        c.execute(f"UPDATE routines SET {', '.join(sets)}, updated_by=?, updated_at=? WHERE id=?",
                  (*args, agent["id"], db.now(), rid))
    return {"deleted": True, "id": rid} if action == "delete" else get(rid)


# ---------------------------------------------------------------- the runner reports

def report(rid: int, data: dict, agent: dict) -> dict:
    """From the runner: a test's result {test: {...}}, or {failed: why, version} to stop it."""
    if not is_runner(agent):
        raise PermissionError(f"only the runner (@{cli.ME}) reports on routines")
    row = _row(rid)
    with db._write_lock, db.conn() as c:
        if isinstance(data.get("test"), dict):
            c.execute("UPDATE routines SET last_test=? WHERE id=?", (json.dumps({**data["test"], "at": db.now()}), rid))
        if data.get("failed"):
            # Only the version it ran: a definition changed since then is not failed by it.
            c.execute("UPDATE routines SET state='failed', failure=? WHERE id=? AND version=? AND state='enabled'",
                      (str(data["failed"])[:2000], rid, data.get("version", row["version"])))
    return get(rid)
