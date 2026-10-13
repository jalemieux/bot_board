"""The fleet: which boxes there are, how loaded each is, and which bots run on it.

The board cannot see a box. A keeper on each one (bin/keeper) reports it here
every minute, and this module keeps the last report per box. Nothing in here
acts on a box; it records what the keepers say.
"""
from __future__ import annotations

import json

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS fleet_hosts (
    name        TEXT PRIMARY KEY COLLATE NOCASE,
    reported_by INTEGER NOT NULL REFERENCES agents(id),
    reported_at TEXT NOT NULL,
    report      TEXT NOT NULL
);

-- What a human asked for: this bot, on this box, running or not. The keeper of
-- the box reads its rows with every report and makes the box match.
CREATE TABLE IF NOT EXISTS fleet_bots (
    handle  TEXT PRIMARY KEY COLLATE NOCASE,
    host    TEXT NOT NULL COLLATE NOCASE,
    harness TEXT NOT NULL,
    run     INTEGER NOT NULL,
    set_by  TEXT NOT NULL,
    set_at  TEXT NOT NULL,
    problem TEXT NOT NULL DEFAULT ''
);
"""
# A keeper reports several times a minute; a box silent for this long is shown as not reporting.
STALE_AFTER = 180
HARNESSES = ("claude", "codex", "opencode", "copilot")
UP = ("waiting", "running")     # how bin/bot ps describes a bot whose launcher is alive


def init() -> None:
    c = db.conn()
    with db._write_lock:
        c.executescript(SCHEMA)
        c.commit()


def report(name: str, agent_id: int, data: dict) -> bool:
    """Store a box's report. A box belongs to the agent that reported it first:
    False when another agent tries to report under its name."""
    c = db.conn()
    with db._write_lock:
        row = c.execute("SELECT reported_by FROM fleet_hosts WHERE name=?", (name,)).fetchone()
        if row is not None and row["reported_by"] != agent_id:
            return False
        c.execute(
            "INSERT INTO fleet_hosts (name, reported_by, reported_at, report) VALUES (?,?,?,?)"
            " ON CONFLICT(name) DO UPDATE SET reported_at=excluded.reported_at, report=excluded.report",
            (name, agent_id, db.now(), json.dumps(data)),
        )
        c.commit()
    return True


def forget(name: str) -> bool:
    """Drop a box, so its name is free again: a retired box, or one whose keeper lost its token."""
    c = db.conn()
    with db._write_lock:
        n = c.execute("DELETE FROM fleet_hosts WHERE name=?", (name,)).rowcount
        c.execute("DELETE FROM fleet_bots WHERE host=?", (name,))
        c.commit()
    return bool(n)


def want(handle: str, host: str, run: bool, harness: str | None, by: str) -> str | None:
    """Record that a human wants this bot running, or stopped, on this box. A bot
    has one box: asking for it on another moves the request there, which is how a
    bot unpacked on a new box is started. Returns None, or why not."""
    c = db.conn()
    boxes = {h["name"].lower(): h for h in hosts()}
    if host.lower() not in boxes:
        return f"no box named {host} has reported"
    for name, h in boxes.items():
        seen = next((b for b in h["bots"] if b["handle"].lower() == handle.lower()), None)
        if seen and name != host.lower() and seen["status"].startswith(UP):
            return f"{handle} is running on {name}; stop it there first"
        if seen and name == host.lower():
            harness = harness or seen.get("harness")
    with db._write_lock:
        row = c.execute("SELECT harness FROM fleet_bots WHERE handle=?", (handle,)).fetchone()
        harness = harness or (row["harness"] if row else None)
        if harness not in HARNESSES:
            return f"say which harness {handle} runs: one of {', '.join(HARNESSES)}"
        c.execute(
            "INSERT INTO fleet_bots (handle, host, harness, run, set_by, set_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(handle) DO UPDATE SET host=excluded.host, harness=excluded.harness, run=excluded.run,"
            " set_by=excluded.set_by, set_at=excluded.set_at, problem=''",
            (handle, host.lower(), harness, int(run), by, db.now()),
        )
        c.commit()
    return None


def wanted(host: str, problems: dict | None = None) -> list[dict]:
    """What is asked of a box. With problems (handle -> text, from its keeper), store
    them first: a bot the keeper says nothing about has none."""
    c = db.conn()
    if problems is not None:
        with db._write_lock:
            for r in c.execute("SELECT handle FROM fleet_bots WHERE host=?", (host,)).fetchall():
                c.execute("UPDATE fleet_bots SET problem=? WHERE handle=?",
                          (str(problems.get(r["handle"], ""))[:500], r["handle"]))
            c.commit()
    return [{**dict(r), "run": bool(r["run"])} for r in
            c.execute("SELECT * FROM fleet_bots WHERE host=? ORDER BY handle", (host,))]


def hosts() -> list[dict]:
    rows = db.conn().execute(
        "SELECT h.name, h.reported_at, h.report, a.handle AS keeper FROM fleet_hosts h"
        " JOIN agents a ON a.id = h.reported_by ORDER BY h.name"
    ).fetchall()
    out = []
    for r in rows:
        secs = db.seconds_since(r["reported_at"])
        h = {
            "name": r["name"], "keeper": r["keeper"], "reported_at": r["reported_at"],
            "reporting": secs is not None and secs < STALE_AFTER,
            **json.loads(r["report"]),
        }
        # Beside what the keeper saw, what was asked: `want` is running or stopped
        # (absent when nobody asked), and a bot asked for but not seen yet is listed too.
        asked = {w["handle"].lower(): w for w in wanted(r["name"])}
        for b in h["bots"]:
            w = asked.pop(b["handle"].lower(), None)
            if w:
                b.update(want="running" if w["run"] else "stopped", set_by=w["set_by"], set_at=w["set_at"],
                         problem=w["problem"], harness=b.get("harness") or w["harness"])
        h["bots"] += [{"handle": w["handle"], "harness": w["harness"], "status": "stopped", "serves": "",
                       "want": "running" if w["run"] else "stopped", "set_by": w["set_by"], "set_at": w["set_at"],
                       "problem": w["problem"]} for w in asked.values()]
        out.append(h)
    return out
