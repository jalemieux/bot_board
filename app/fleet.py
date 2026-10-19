"""The fleet: which boxes there are, how loaded each is, and which bots run on it.

The board cannot see a box. A keeper on each one (bin/keeper) reports it here
every minute, and this module keeps the last report per box. Nothing in here
acts on a box; it records what the keepers say.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import uuid
from datetime import datetime, timezone

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

CREATE TABLE IF NOT EXISTS fleet_alert_clears (
    handle TEXT PRIMARY KEY COLLATE NOCASE,
    host TEXT NOT NULL COLLATE NOCASE,
    health TEXT NOT NULL,
    last_run TEXT NOT NULL,
    problem TEXT NOT NULL,
    set_at TEXT NOT NULL
);

-- Secrets every box's bots get in their environment (GH_TOKEN, ...), set by an admin
-- on /fleet. Only keepers read the values; everyone else sees names and who set them.
CREATE TABLE IF NOT EXISTS fleet_secrets (
    name   TEXT PRIMARY KEY,
    value  TEXT NOT NULL,
    set_by TEXT NOT NULL,
    set_at TEXT NOT NULL
);
"""
# What a secret may be called: the names bin/bot and the keeper accept from fleet.env,
# so a secret cannot move a box's BOARD or PATH.
SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]*_(?:TOKEN|KEY|SECRET)")
# A move, in two steps: move_to is set on the row while the bot's own box packs it
# and hands the archive over; then the row points at the new box with fetch naming
# the archive, until that box has unpacked it. The archive holds the bot's token,
# so it travels box to box over ssh (KEEPER_HANDOVER), never through the board.
MOVE_COLUMNS = ("move_to", "fetch")
# kill: when a human asked for the bot to stop now, cutting its harness run short
# (a stuck or runaway bot); cleared by the next request for it.
EXTRA_COLUMNS = MOVE_COLUMNS + ("kill",)
# A keeper reports several times a minute; a box silent for this long is shown as not reporting.
STALE_AFTER = 180
HARNESSES = ("claude", "codex", "opencode", "copilot")
UP = ("waiting", "running")     # how bin/bot ps describes a bot whose launcher is alive
# The first names bin/bot draws a new bot's handle from (its BOT_NAMES default).
NAMES = ("ada", "alma", "amos", "anya", "arlo", "basil", "bea", "bruno", "cleo", "cora", "dara", "dex",
         "edie", "eli", "elsa", "enzo", "esme", "ezra", "faye", "finn", "flora", "gus", "hana", "hugo",
         "ida", "igor", "ines", "iris", "ivo", "jade", "jonas", "juno", "kai", "kira", "lars", "lena",
         "leo", "lila", "linus", "lola", "luca", "mabel", "mara", "milo", "mina", "nell", "nico", "nina",
         "noor", "olga", "omar", "opal", "otis", "otto", "pia", "quinn", "rafa", "remy", "rosa", "rufus",
         "sana", "seth", "sven", "tess", "theo", "tilda", "uma", "vera", "wes", "willa", "yara", "zane",
         "zoe",)


def init() -> None:
    c = db.conn()
    with db._write_lock:
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("PRAGMA table_info(fleet_bots)")}
        # project: the project a new bot should be dedicated to once it comes online (deferred
        # because a bot has no agent row until its keeper starts it and it registers).
        for col in EXTRA_COLUMNS + ("project",):
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
    # A box's report is when its newly-started bots become known; honor any project a
    # bot was asked to be dedicated to when it was created, now that it is online.
    try:
        from . import projects
        projects.apply_pending_dedications()
    except Exception:
        pass
    return True


def secrets() -> list[dict]:
    """The fleet's secrets without their values: name, who set it, when, and its last 4 characters."""
    return [{"name": r["name"], "set_by": r["set_by"], "set_at": r["set_at"], "ends": r["value"][-4:]}
            for r in db.conn().execute("SELECT * FROM fleet_secrets ORDER BY name")]


def secret_values() -> dict[str, str]:
    """name -> value. For keepers only."""
    return {r["name"]: r["value"] for r in db.conn().execute("SELECT name, value FROM fleet_secrets ORDER BY name")}


def secrets_version() -> str:
    """Changes whenever a secret is added, replaced or removed; keepers fetch the values when it does."""
    return hashlib.sha256(json.dumps(secret_values(), sort_keys=True).encode()).hexdigest()[:16]


def set_secret(name: str, value: str, by: str) -> None:
    c = db.conn()
    with db._write_lock:
        c.execute("INSERT INTO fleet_secrets (name, value, set_by, set_at) VALUES (?,?,?,?)"
                  " ON CONFLICT(name) DO UPDATE SET value=excluded.value, set_by=excluded.set_by,"
                  " set_at=excluded.set_at", (name, value, by, db.now()))
        c.commit()


def delete_secret(name: str) -> bool:
    c = db.conn()
    with db._write_lock:
        n = c.execute("DELETE FROM fleet_secrets WHERE name=?", (name,)).rowcount
        c.commit()
    return bool(n)


def keeps_a_box(agent_id: int) -> bool:
    return db.conn().execute("SELECT 1 FROM fleet_hosts WHERE reported_by=?", (agent_id,)).fetchone() is not None


def forget(name: str) -> bool:
    """Drop a box, so its name is free again: a retired box, or one whose keeper lost its token."""
    c = db.conn()
    with db._write_lock:
        n = c.execute("DELETE FROM fleet_hosts WHERE name=?", (name,)).rowcount
        c.execute("DELETE FROM fleet_bots WHERE host=?", (name,))
        c.commit()
    return bool(n)


def _set_want(c, handle, host, harness, run, by, now, kill=False, project=None):
    c.execute(
        "INSERT INTO fleet_bots (handle, host, harness, run, set_by, set_at, kill, project) VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(handle) DO UPDATE SET host=excluded.host, harness=excluded.harness, run=excluded.run,"
        " set_by=excluded.set_by, set_at=excluded.set_at, problem='', move_to=NULL, kill=excluded.kill",
        (handle, host.lower(), harness, int(run), by, now, now if kill else None, project or None),
    )


def want(handle: str, host: str, run: bool, harness: str | None, by: str, kill: bool = False,
         project: str | None = None) -> str | None:
    """Record that a human wants this bot running, or stopped, on this box. A bot
    has one box: asking for it on another moves the request there, which is how a
    bot unpacked on a new box is started. kill: stop it now, without waiting for the
    harness run it is in. project: dedicate the bot to this project once it comes
    online (kept on the row until applied). Returns None, or why not."""
    if kill and run:
        return "a kill stops the bot: ask for run false"
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
        now = db.now()
        if harness not in HARNESSES:
            return f"say which harness {handle} runs: one of {', '.join(HARNESSES)}"
        _set_want(c, handle, host, harness, run, by, now, kill, project)
        c.commit()
    return None


def bot_state(b: dict, reporting: bool = True) -> dict:
    """What a bot is doing, in the one vocabulary /fleet, /projects and /status share (#100):
    {state, words, reason, can_work}. can_work: it would take a message now, or once its
    current run ends; a bot that cannot (logged out, out of usage, on a box gone dark) is
    neither counted as up nor offered for a project's pool."""
    st, health, problem = b.get("status") or "", b.get("health") or "", b.get("problem") or ""
    up = st.startswith(UP)
    doing = st[len("running "):].split(",")[0] if st.startswith("running ") else ""

    def out(state, words, reason="", can_work=False):
        return {"state": state, "words": words, "reason": reason, "can_work": can_work}
    if not reporting:
        return out("unknown", f"{st or 'stopped'} when last heard", "its box is not reporting")
    if b.get("move_to"):
        return out("moving", f"moving to {b['move_to']}: " + ("stopping" if up else "packing"))
    if b.get("fetch") and not up:
        return out("moving", "arriving: unpacking")
    if (up or b.get("want") == "running") and health.startswith("out of") and " capacity since " in health:
        return out("at capacity", "model temporarily at capacity", health)
    if (up or b.get("want") == "running") and health.startswith("out of"):
        return out("out of usage", "usage limit reached", health)
    if b.get("want") == "running" and not up and st != "moved":
        return out("failing", "not running", problem) if problem else out("starting", "starting")
    if up and ("stopping" in st or b.get("want") == "stopped"):
        # asked, and the keeper has not acted yet: say what was asked; once it has, "stopping"
        words = "stopping" if "stopping" in st else ""
        words = ", ".join(w for w in (words, "kill asked" if b.get("kill") else "" if words else "stop asked") if w)
        return out("stopping", words)
    if up and health.startswith("logged out"):
        return out("needs login", "needs a login", health)
    if up and health.startswith("out of"):
        return out("out of usage", "out of usage", health)
    if up:
        words = f"working on {doing}" if doing else "working" if st.startswith("running") else "waiting for work"
        # a failed last run is worth seeing, but the bot still takes the next message
        return out("failing" if health else "working" if st.startswith("running") else "waiting", words, health, True)
    return out("stopped", "stopped" if st != "moved" else "moved to another box", problem)


def pools() -> list[dict]:
    """Projects with a dedicated bot pool, and its bots (none: no pool bot works there)."""
    c = db.conn()
    try:
        rows = c.execute("SELECT p.id, p.slug FROM project_pools pp JOIN projects p ON p.id = pp.project_id"
                         " WHERE pp.configured = 1 AND p.archived = 0 ORDER BY p.slug").fetchall()
    except db.sqlite3.OperationalError:     # a board without projects yet
        return []
    return [{"project": r["slug"], "bots": [b[0] for b in c.execute(
        "SELECT a.handle FROM project_pool_bots pb JOIN agents a ON a.id = pb.agent_id"
        " WHERE pb.project_id = ? ORDER BY a.handle COLLATE NOCASE", (r["id"],))]} for r in rows]


def down(handles) -> dict[str, str]:
    """Of these thread workers, the bots not running now, for the pickup chip (#169):
    'stopped' when every box that knows the bot reports it stopped, else 'away' for a bot
    the board has not seen for an hour (one run by hand, or on a box gone dark)."""
    handles = {h.lower(): h for h in handles if h}
    if not handles:
        return {}
    states = {}
    for h in hosts():
        for b in h["bots"]:
            if b["handle"].lower() in handles:
                states.setdefault(b["handle"].lower(), set()).add(b["state"]["state"])
    out, unsure = {}, []
    for low, handle in handles.items():
        seen = states.get(low, set())
        if seen == {"stopped"}:
            out[handle] = "stopped"
        elif seen <= {"unknown"}:
            unsure.append(low)
    if unsure:
        marks = ",".join("?" * len(unsure))
        for a in db.conn().execute("SELECT handle, kind, last_seen_at FROM agents"
                                   f" WHERE lower(handle) IN ({marks})", unsure):
            if a["kind"] != "human" and db.presence(a["last_seen_at"]) in ("idle", "away"):
                out[handles[a["handle"].lower()]] = "away"
    return out


def free_name() -> str:
    """A first name for a new bot that no agent on the board, no request and no box
    has used; once those run out, a name with four hex characters, as bin/bot does."""
    c = db.conn()
    taken = {r[0].lower() for r in c.execute("SELECT handle FROM agents")}
    taken |= {r[0].lower() for r in c.execute("SELECT handle FROM fleet_bots")}
    taken |= {b["handle"].lower() for h in hosts() for b in h["bots"]}
    free = [n for n in NAMES if n not in taken]
    return random.choice(free) if free else f"{random.choice(NAMES)}-{uuid.uuid4().hex[:4]}"


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
        return f"{handle} is a single-repo bot; only pool bots move from the board (bin/bot pack and unpack by hand)"
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
            " set_by=excluded.set_by, set_at=excluded.set_at, problem='', move_to=excluded.move_to, fetch=NULL, kill=NULL",
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
            skipped[b["handle"]] = "a single-repo bot; move it by hand"
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
            raw = c.execute("SELECT report FROM fleet_hosts WHERE name=?", (host,)).fetchone()
            observed = {b["handle"].lower(): b for b in json.loads(raw["report"])["bots"]} if raw else {}
            for r in c.execute("SELECT handle FROM fleet_bots WHERE host=?", (host,)).fetchall():
                problem = str(problems.get(r["handle"], ""))[:500]
                ack = c.execute("SELECT a.* FROM fleet_alert_clears a JOIN fleet_bots b ON b.handle=a.handle "
                                "AND b.set_at=a.set_at WHERE a.handle=? AND a.host=?", (r["handle"], host)).fetchone()
                bot = observed.get(r["handle"].lower(), {})
                if ack and bot.get("status") == "stopped" and problem == ack["problem"] and bot.get("last_run", "") == ack["last_run"]:
                    problem = ""
                c.execute("UPDATE fleet_bots SET problem=? WHERE handle=?", (problem, r["handle"]))
            c.commit()
    return [{**dict(r), "run": bool(r["run"])} for r in
            c.execute("SELECT * FROM fleet_bots WHERE host=? ORDER BY handle", (host,))]


def alert_key(host, bot):
    fields = {k: bot.get(k, "") for k in ("status", "health", "last_run", "problem", "move_to", "fetch", "kill", "want", "set_at", "harness")}
    return hashlib.sha256(json.dumps([host.lower(), bot["handle"].lower(), fields], sort_keys=True).encode()).hexdigest()


def clear_alerts(items: list[dict], by: str):
    with db._write_lock, db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        boxes = {h["name"].lower(): h for h in hosts()}
        checked = []
        for item in items:
            host, handle = item["host"].lower(), item["handle"].lower()
            box = boxes.get(host)
            if not box or not box["reporting"]:
                raise ValueError("box state is unknown; wait for a fresh report")
            why = not_operator(box, by)
            if why:
                raise PermissionError(why)
            bot = next((b for b in box["bots"] if b["handle"].lower() == handle), None)
            if not bot or not bot.get("clear_key") or bot["clear_key"] != item["key"]:
                raise ValueError("worker state or alert changed; refresh before clearing")
            for other in boxes.values():
                for copy in other["bots"]:
                    if other["name"].lower() != host and copy["handle"].lower() == handle and copy["status"] != "moved":
                        if not other["reporting"] or copy["status"] != "stopped":
                            raise ValueError("worker is live or unknown on another box")
            checked.append((host, handle, bot))
        # One atomic group: a changed/live member prevents clearing any member.
        for host, handle, bot in checked:
            stamp = datetime.now(timezone.utc).isoformat()
            _set_want(c, handle, host, bot["harness"], False, by, stamp)
            c.execute("UPDATE fleet_bots SET fetch=NULL WHERE handle=?", (handle,))
            c.execute("INSERT INTO fleet_alert_clears VALUES (?,?,?,?,?,?) ON CONFLICT(handle) DO UPDATE SET "
                      "host=excluded.host,health=excluded.health,last_run=excluded.last_run,problem=excluded.problem,set_at=excluded.set_at",
                      (handle, host, bot.get("health", ""), bot.get("last_run", ""), bot.get("problem", ""), stamp))
    return [handle for _, handle, _ in checked]


def hosts() -> list[dict]:
    rows = db.conn().execute(
        "SELECT h.name, h.reported_at, h.report, a.handle AS keeper FROM fleet_hosts h"
        " JOIN agents a ON a.id = h.reported_by ORDER BY h.name"
    ).fetchall()
    # when each bot last posted, from the board's side of what it does
    posted = {p["handle"].lower(): p["at"] for p in db.conn().execute(
        "SELECT a.handle, MAX(m.created_at) AS at FROM messages m JOIN agents a ON a.id = m.agent_id GROUP BY a.id")}
    # each bot's dedicated project, if a project's bot pool names it
    try:
        dedicated = {r["handle"].lower(): r["slug"] for r in db.conn().execute(
            "SELECT a.handle, p.slug FROM project_pool_bots pb JOIN agents a ON a.id = pb.agent_id"
            " JOIN projects p ON p.id = pb.project_id WHERE p.archived = 0")}
    except db.sqlite3.OperationalError:     # a board without projects yet
        dedicated = {}
    out = []
    clears = {r["handle"].lower(): dict(r) for r in db.conn().execute("SELECT * FROM fleet_alert_clears")}
    for r in rows:
        secs = db.seconds_since(r["reported_at"])
        h = {
            "name": r["name"], "keeper": r["keeper"], "reported_at": r["reported_at"],
            "reporting": secs is not None and secs < STALE_AFTER,
            **json.loads(r["report"]),
        }
        # Beside what the keeper saw, what was asked: `want` is running or stopped
        # (absent when nobody asked), and a bot asked for but not seen yet is listed too.
        observed = {b["handle"].lower() for b in h["bots"]}
        asked = {w["handle"].lower(): w for w in wanted(r["name"])}
        for b in h["bots"]:
            w = asked.pop(b["handle"].lower(), None)
            if w:
                b.update(want="running" if w["run"] else "stopped", set_by=w["set_by"], set_at=w["set_at"],
                         problem=w["problem"], harness=b.get("harness") or w["harness"],
                         move_to=w["move_to"], fetch=w["fetch"], kill=w["kill"])
        h["bots"] += [{"handle": w["handle"], "harness": w["harness"], "status": "stopped", "serves": "",
                       "want": "running" if w["run"] else "stopped", "set_by": w["set_by"], "set_at": w["set_at"],
                       "problem": w["problem"], "move_to": w["move_to"], "fetch": w["fetch"]} for w in asked.values()]
        for b in h["bots"]:
            ack = clears.get(b["handle"].lower())
            if ack and h["reporting"] and b["status"] == "stopped" and ack["host"].lower() == h["name"].lower():
                if ack["set_at"] == b.get("set_at") and ack["health"] == b.get("health", "") and ack["last_run"] == b.get("last_run", ""):
                    b["health"] = ""
            if h["reporting"] and b["handle"].lower() in observed and b["status"] == "stopped" and any(b.get(k) for k in ("health", "problem", "move_to", "fetch")):
                b["clear_key"] = alert_key(h["name"], b)
            b["last_post"] = posted.get(b["handle"].lower(), "")
            b["project"] = dedicated.get(b["handle"].lower())
            b["state"] = bot_state(b, h["reporting"])
        out.append(h)
    return out
