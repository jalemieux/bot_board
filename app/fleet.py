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
"""
# A keeper reports every minute; a box silent for this long is shown as not reporting.
STALE_AFTER = 180


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


def hosts() -> list[dict]:
    rows = db.conn().execute(
        "SELECT h.name, h.reported_at, h.report, a.handle AS keeper FROM fleet_hosts h"
        " JOIN agents a ON a.id = h.reported_by ORDER BY h.name"
    ).fetchall()
    out = []
    for r in rows:
        secs = db.seconds_since(r["reported_at"])
        out.append({
            "name": r["name"], "keeper": r["keeper"], "reported_at": r["reported_at"],
            "reporting": secs is not None and secs < STALE_AFTER,
            **json.loads(r["report"]),
        })
    return out
