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
# A move, in two steps: move_to is set on the row while the bot's own box packs it
# and hands the archive over; then the row points at the new box with fetch naming
# the archive, until that box has unpacked it. The archive holds the bot's token,
# so it travels box to box over ssh (KEEPER_HANDOVER), never through the board.
MOVE_COLUMNS = ("move_to", "fetch")
# A keeper reports several times a minute; a box silent for this long is shown as not reporting.
STALE_AFTER = 180
HARNESSES = ("claude", "codex", "opencode", "copilot")
UP = ("waiting", "running")     # how bin/bot ps describes a bot whose launcher is alive


def init() -> None:
    c = db.conn()
    with db._write_lock:
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("PRAGMA table_info(fleet_bots)")}
        for col in MOVE_COLUMNS:
            if col not in have:
                c.execute(f"ALTER TABLE fleet_bots ADD COLUMN {col} TEXT")
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
    why = not_operator(boxes[host.lower()], by)
    if why:
        return why
    find = lambda h: next((b for b in h["bots"] if b["handle"].lower() == handle.lower()), None)
    here = find(boxes[host.lower()])
    harness = harness or (here or {}).get("harness")
    # Only a start is held back, only by a box that is reporting now, and not when the
    # bot is already up on the box asked for: a stop is never refused, a box that went
    # dark does not hold its bots for ever, and another box's word cannot lock a bot
    # where it really runs.
    if run and not (here and here["status"].startswith(UP)):
        for name, h in boxes.items():
            seen = find(h)
            if seen and name != host.lower() and h["reporting"] and seen["status"].startswith(UP):
                return f"{handle} is running on {name}; stop it there first"
    with db._write_lock:
        row = c.execute("SELECT harness FROM fleet_bots WHERE handle=?", (handle,)).fetchone()
        harness = harness or (row["harness"] if row else None)
        if harness not in HARNESSES:
            return f"say which harness {handle} runs: one of {', '.join(HARNESSES)}"
        c.execute(
            "INSERT INTO fleet_bots (handle, host, harness, run, set_by, set_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(handle) DO UPDATE SET host=excluded.host, harness=excluded.harness, run=excluded.run,"
            " set_by=excluded.set_by, set_at=excluded.set_at, problem='', move_to=NULL",
            (handle, host.lower(), harness, int(run), by, db.now()),
        )
        c.commit()
    return None


def move(handle: str, to: str, by: str) -> str | None:
    """Record that a human wants this bot on another box. Its own box's keeper stops
    it, packs it and hands the archive over; the board then points the bot at the new
    box (handed), whose keeper unpacks it and starts it (fetched). Returns None, or why not."""
    boxes = {h["name"].lower(): h for h in hosts()}
    to = to.lower()
    if to not in boxes or not boxes[to]["reporting"]:
        return f"no box named {to} is reporting"
    c = db.conn()
    row = c.execute("SELECT * FROM fleet_bots WHERE handle=?", (handle,)).fetchone()
    # the bot's box: where it was asked to be, else the reporting box that has it
    src = row["host"] if row else next((n for n, h in boxes.items() if h["reporting"] and
                                        any(b["handle"].lower() == handle.lower() and b["status"] != "moved"
                                            for b in h["bots"])), None)
    if not src or src not in boxes:
        return f"no box reports {handle}"
    if row and row["fetch"]:
        return f"{handle} is still being unpacked on {src}; move it once it is there"
    if src == to:
        return f"{handle} is on {to} already"
    if not boxes[src]["reporting"]:
        return f"{src} is not reporting, so nothing can pack {handle} there"
    seen = next((b for b in boxes[src]["bots"] if b["handle"].lower() == handle.lower()), None)
    if not seen:
        return f"{src} does not report {handle}"
    if not seen.get("pool"):
        return f"{handle} is dedicated to one repo; only pool bots move from the board (bin/bot pack and unpack by hand)"
    harness = seen.get("harness") or (row["harness"] if row else None)
    if harness not in HARNESSES:
        return f"{src} does not say which harness {handle} runs"
    for box in (boxes[src], boxes[to]):
        why = not_operator(box, by)
        if why:
            return why
    with db._write_lock:
        c.execute(
            "INSERT INTO fleet_bots (handle, host, harness, run, set_by, set_at, move_to) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(handle) DO UPDATE SET host=excluded.host, harness=excluded.harness, run=excluded.run,"
            " set_by=excluded.set_by, set_at=excluded.set_at, problem='', move_to=excluded.move_to, fetch=NULL",
            # a bot that was stopped stays stopped on the new box
            (handle, src, harness, int(seen["status"].startswith(UP) or bool(row and row["run"])), by, db.now(), to),
        )
        c.commit()
    return None


def drain(host: str, by: str, to: str | None = None) -> dict | str:
    """Move every pool bot up on a box to other boxes, one move() each: a full box
    emptied in one request. Each bot goes to `to`, else to the reporting box with
    the least load per core, counting the bots this drain already sent there.
    Returns {"moved": {handle: box}, "skipped": {handle: why}}, or why nothing can be done."""
    boxes = {h["name"].lower(): h for h in hosts()}
    src = boxes.get(host.lower())
    if not src:
        return f"no box named {host} has reported"
    targets = [h for n, h in boxes.items() if n != host.lower() and h["reporting"]]
    if to:
        targets = [h for h in targets if h["name"].lower() == to.lower()]
        if not targets:
            return f"no other box named {to} is reporting"
    if not targets:
        return "no other box is reporting, so there is nowhere to move the bots"
    sent = {h["name"]: 0 for h in targets}

    def weight(h):   # load per core now, plus one per bot this drain already sent there
        cores = h.get("cores") or 1
        return ((h.get("load") or 0) + sent[h["name"]]) / cores
    moved, skipped = {}, {}
    for b in src["bots"]:
        if not b["status"].startswith(UP):
            continue
        if b.get("move_to") or b.get("fetch"):
            skipped[b["handle"]] = "already moving"
            continue
        if not b.get("pool"):
            skipped[b["handle"]] = "dedicated to one repo; move it by hand"
            continue
        dest = min(targets, key=weight)
        why = move(b["handle"], dest["name"], by)
        if why:
            skipped[b["handle"]] = why
        else:
            moved[b["handle"]] = dest["name"]
            sent[dest["name"]] += 1
    if not moved and not skipped:
        return f"no bot is running on {host}"
    return {"moved": moved, "skipped": skipped}


def moved(host: str, handed: dict, fetched: list) -> None:
    """A keeper's word on moves: handed (handle -> archive name) it packed and handed
    over, so the bot now belongs to the box it is going to; fetched, it unpacked."""
    c = db.conn()
    with db._write_lock:
        for h, archive in handed.items():
            c.execute("UPDATE fleet_bots SET host=move_to, fetch=?, move_to=NULL, problem='' WHERE handle=?"
                      " AND host=? AND move_to IS NOT NULL", (str(archive)[:200], h, host))
        for h in fetched:
            c.execute("UPDATE fleet_bots SET fetch=NULL WHERE handle=? AND host=?", (h, host))
        c.commit()


def not_operator(box: dict, by: str) -> str | None:
    """Why this human's requests are not for this box, when its keeper named its operators."""
    ops = [o.lower() for o in box.get("operators") or []]
    if ops and by.lower() not in ops:
        return f"@{by} is not an operator of {box['name']} (its KEEPER_OPERATORS)"
    return None


def unwant(handle: str, by: str) -> str | None:
    """Drop what was asked for a bot: its box's keeper leaves it as it is from then on.
    Returns None, or why not."""
    c = db.conn()
    row = c.execute("SELECT host FROM fleet_bots WHERE handle=?", (handle,)).fetchone()
    if row is None:
        return ""
    box = next((h for h in hosts() if h["name"].lower() == row["host"].lower()), None)
    why = box and not_operator(box, by)
    if why:
        return why
    with db._write_lock:
        c.execute("DELETE FROM fleet_bots WHERE handle=?", (handle,))
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
    # when each bot last posted, from the board's side of what it does
    posted = {p["handle"].lower(): p["at"] for p in db.conn().execute(
        "SELECT a.handle, MAX(m.created_at) AS at FROM messages m JOIN agents a ON a.id = m.agent_id GROUP BY a.id")}
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
                         problem=w["problem"], harness=b.get("harness") or w["harness"],
                         move_to=w["move_to"], fetch=w["fetch"])
        h["bots"] += [{"handle": w["handle"], "harness": w["harness"], "status": "stopped", "serves": "",
                       "want": "running" if w["run"] else "stopped", "set_by": w["set_by"], "set_at": w["set_at"],
                       "problem": w["problem"], "move_to": w["move_to"], "fetch": w["fetch"]} for w in asked.values()]
        for b in h["bots"]:
            b["last_post"] = posted.get(b["handle"].lower(), "")
        out.append(h)
    return out
