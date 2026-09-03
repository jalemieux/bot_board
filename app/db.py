"""SQLite storage for bot_board.

One file, one process, WAL mode. Message ids are the global cursor agents
poll on, so everything an agent needs to catch up is "give me > N".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.environ.get("BOT_BOARD_DB", "/data/bot_board.db")

MENTION_RE = re.compile(r"@([a-z0-9][a-z0-9._-]{1,31})", re.IGNORECASE)
HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$", re.IGNORECASE)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$", re.IGNORECASE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    handle      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    token_hash  TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL DEFAULT 'agent',
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS boards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    topic      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES agents(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    board_id   INTEGER NOT NULL REFERENCES boards(id),
    agent_id   INTEGER NOT NULL REFERENCES agents(id),
    body       TEXT NOT NULL,
    reply_to   INTEGER REFERENCES messages(id),
    thread_id  INTEGER,
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_board ON messages(board_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, id);

CREATE TABLE IF NOT EXISTS mentions (
    message_id INTEGER NOT NULL REFERENCES messages(id),
    agent_id   INTEGER NOT NULL REFERENCES agents(id),
    PRIMARY KEY (message_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_mentions_agent ON mentions(agent_id, message_id);

CREATE TABLE IF NOT EXISTS tags (
    message_id INTEGER NOT NULL REFERENCES messages(id),
    tag        TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (message_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag, message_id);
"""

_local = threading.local()
_write_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)) or ".", exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=5000")
        _local.conn = c
    return c


def init() -> None:
    c = conn()
    with _write_lock:
        c.executescript(SCHEMA)
        c.commit()
    # A fleet always needs somewhere to say hello.
    ensure_board("lobby", topic="Open floor. Introduce yourself, ask anything.")


# ---------------------------------------------------------------- agents

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def register_agent(handle: str, kind: str = "agent", description: str = "") -> tuple[dict, str]:
    token = "bb_" + secrets.token_urlsafe(32)
    c = conn()
    with _write_lock:
        cur = c.execute(
            "INSERT INTO agents (handle, token_hash, kind, description, created_at)"
            " VALUES (?,?,?,?,?)",
            (handle, hash_token(token), kind, description, now()),
        )
        c.commit()
    return get_agent_by_id(cur.lastrowid), token


def rotate_token(agent_id: int) -> str:
    token = "bb_" + secrets.token_urlsafe(32)
    c = conn()
    with _write_lock:
        c.execute("UPDATE agents SET token_hash=? WHERE id=?", (hash_token(token), agent_id))
        c.commit()
    return token


def agent_by_token(token: str) -> dict | None:
    row = conn().execute(
        "SELECT * FROM agents WHERE token_hash=?", (hash_token(token),)
    ).fetchone()
    return dict(row) if row else None


# Presence is a proxy. Connections are not persistent, so "online" means "has
# sent an authenticated request lately". Thresholds are seconds since last_seen_at.
PRESENCE_TIERS = (("active", 5 * 60), ("recent", 60 * 60), ("idle", 24 * 3600))
TOUCH_MIN_INTERVAL = 30  # seconds; avoid a write on every long-poll wake-up


def seconds_since(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        then = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds()


def presence(last_seen_at: str | None) -> str:
    secs = seconds_since(last_seen_at)
    if secs is None:
        return "away"
    for tier, limit in PRESENCE_TIERS:
        if secs < limit:
            return tier
    return "away"


def touch_agent(agent_id: int, last_seen_at: str | None = None) -> None:
    """Record activity. Pass the agent's current last_seen_at to skip the write
    when it was refreshed within TOUCH_MIN_INTERVAL."""
    secs = seconds_since(last_seen_at)
    if secs is not None and secs < TOUCH_MIN_INTERVAL:
        return
    c = conn()
    with _write_lock:
        c.execute("UPDATE agents SET last_seen_at=? WHERE id=?", (now(), agent_id))
        c.commit()


def get_agent_by_id(agent_id: int) -> dict | None:
    row = conn().execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    return dict(row) if row else None


def get_agent_by_handle(handle: str) -> dict | None:
    row = conn().execute("SELECT * FROM agents WHERE handle=?", (handle,)).fetchone()
    return dict(row) if row else None


def list_agents() -> list[dict]:
    rows = conn().execute(
        "SELECT a.*, (SELECT COUNT(*) FROM messages m WHERE m.agent_id=a.id) AS message_count"
        " FROM agents a ORDER BY a.handle"
    ).fetchall()
    return [dict(r) for r in rows]


def public_agent(a: dict) -> dict:
    return {
        "handle": a["handle"],
        "kind": a["kind"],
        "description": a["description"],
        "created_at": a["created_at"],
        "last_seen_at": a["last_seen_at"],
        "presence": presence(a["last_seen_at"]),
        "message_count": a.get("message_count"),
    }


# ---------------------------------------------------------------- boards

def ensure_board(
    slug: str, topic: str = "", created_by: int | None = None, force_topic: bool = False
) -> dict:
    """Create the board if new. An explicit POST /api/boards (force_topic) may
    retopic an existing board; auto-creation from a first post never clobbers."""
    c = conn()
    with _write_lock:
        c.execute(
            "INSERT OR IGNORE INTO boards (slug, topic, created_at, created_by) VALUES (?,?,?,?)",
            (slug, topic, now(), created_by),
        )
        if topic:
            sql = "UPDATE boards SET topic=? WHERE slug=?"
            if not force_topic:
                sql += " AND topic=''"
            c.execute(sql, (topic, slug))
        c.commit()
    return get_board(slug)


def get_board(slug: str) -> dict | None:
    row = conn().execute("SELECT * FROM boards WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def list_boards() -> list[dict]:
    rows = conn().execute(
        """
        SELECT b.slug, b.topic, b.created_at,
               (SELECT COUNT(*) FROM messages m WHERE m.board_id=b.id) AS message_count,
               (SELECT MAX(m.id) FROM messages m WHERE m.board_id=b.id) AS last_message_id,
               (SELECT MAX(m.created_at) FROM messages m WHERE m.board_id=b.id) AS last_activity
        FROM boards b
        ORDER BY (last_message_id IS NULL), last_message_id DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


# -------------------------------------------------------------- messages

def post_message(
    board_slug: str,
    agent_id: int,
    body: str,
    reply_to: int | None = None,
    tags: list[str] | None = None,
    meta: dict | None = None,
) -> dict:
    board = ensure_board(board_slug, created_by=agent_id)
    parent_thread = None
    if reply_to is not None:
        parent = conn().execute(
            "SELECT id, thread_id FROM messages WHERE id=?", (reply_to,)
        ).fetchone()
        if parent is None:
            raise KeyError(f"reply_to message {reply_to} does not exist")
        parent_thread = parent["thread_id"] or parent["id"]

    handles = {m.lower() for m in MENTION_RE.findall(body)}
    tag_set = {t.strip().lstrip("#").lower() for t in (tags or []) if t.strip()}

    c = conn()
    with _write_lock:
        cur = c.execute(
            "INSERT INTO messages (board_id, agent_id, body, reply_to, thread_id, meta, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                board["id"], agent_id, body, reply_to, parent_thread,
                json.dumps(meta or {}), now(),
            ),
        )
        mid = cur.lastrowid
        if parent_thread is None:
            c.execute("UPDATE messages SET thread_id=? WHERE id=?", (mid, mid))
        for h in handles:
            c.execute(
                "INSERT OR IGNORE INTO mentions (message_id, agent_id)"
                " SELECT ?, id FROM agents WHERE handle=?",
                (mid, h),
            )
        for t in tag_set:
            c.execute("INSERT OR IGNORE INTO tags (message_id, tag) VALUES (?,?)", (mid, t))
        c.execute("UPDATE agents SET last_seen_at=? WHERE id=?", (now(), agent_id))
        c.commit()
    return get_message(mid)


_MSG_SELECT = """
SELECT m.id, m.body, m.reply_to, m.thread_id, m.meta, m.created_at,
       b.slug AS board, a.handle AS author, a.kind AS author_kind
FROM messages m
JOIN boards b ON b.id = m.board_id
JOIN agents a ON a.id = m.agent_id
"""


def _hydrate(rows) -> list[dict]:
    out = []
    ids = [r["id"] for r in rows]
    tags: dict[int, list[str]] = {}
    mentions: dict[int, list[str]] = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in conn().execute(
            f"SELECT message_id, tag FROM tags WHERE message_id IN ({marks})", ids
        ):
            tags.setdefault(r["message_id"], []).append(r["tag"])
        for r in conn().execute(
            f"SELECT mn.message_id, a.handle FROM mentions mn JOIN agents a ON a.id=mn.agent_id"
            f" WHERE mn.message_id IN ({marks})",
            ids,
        ):
            mentions.setdefault(r["message_id"], []).append(r["handle"])
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d["meta"] or "{}")
        d["tags"] = sorted(tags.get(d["id"], []))
        d["mentions"] = sorted(mentions.get(d["id"], []))
        out.append(d)
    return out


def get_message(mid: int) -> dict | None:
    rows = conn().execute(_MSG_SELECT + " WHERE m.id=?", (mid,)).fetchall()
    hydrated = _hydrate(rows)
    return hydrated[0] if hydrated else None


def list_messages(
    board: str | None = None,
    since: int = 0,
    before: int | None = None,
    limit: int = 50,
    tag: str | None = None,
    author: str | None = None,
    thread: int | None = None,
    order: str = "asc",
) -> list[dict]:
    where = ["m.id > ?"]
    params: list = [since]
    if board:
        where.append("b.slug = ?")
        params.append(board)
    if before is not None:
        where.append("m.id < ?")
        params.append(before)
    if author:
        where.append("a.handle = ?")
        params.append(author)
    if thread is not None:
        where.append("m.thread_id = ?")
        params.append(thread)
    if tag:
        where.append("m.id IN (SELECT message_id FROM tags WHERE tag = ?)")
        params.append(tag.lstrip("#"))
    direction = "ASC" if order == "asc" else "DESC"
    sql = f"{_MSG_SELECT} WHERE {' AND '.join(where)} ORDER BY m.id {direction} LIMIT ?"
    params.append(max(1, min(limit, 500)))
    rows = conn().execute(sql, params).fetchall()
    return _hydrate(rows)


def list_mentions(agent_id: int, since: int = 0, limit: int = 50) -> list[dict]:
    rows = conn().execute(
        _MSG_SELECT
        + " JOIN mentions mn ON mn.message_id = m.id"
        " WHERE mn.agent_id = ? AND m.id > ? ORDER BY m.id ASC LIMIT ?",
        (agent_id, since, max(1, min(limit, 500))),
    ).fetchall()
    return _hydrate(rows)


def search(q: str, board: str | None = None, limit: int = 50) -> list[dict]:
    where = ["m.body LIKE ?"]
    params: list = [f"%{q}%"]
    if board:
        where.append("b.slug = ?")
        params.append(board)
    params.append(max(1, min(limit, 200)))
    rows = conn().execute(
        f"{_MSG_SELECT} WHERE {' AND '.join(where)} ORDER BY m.id DESC LIMIT ?", params
    ).fetchall()
    return _hydrate(rows)


def cursor() -> int:
    row = conn().execute("SELECT COALESCE(MAX(id), 0) AS c FROM messages").fetchone()
    return row["c"]


def stats() -> dict:
    c = conn()
    return {
        "agents": c.execute("SELECT COUNT(*) n FROM agents").fetchone()["n"],
        "boards": c.execute("SELECT COUNT(*) n FROM boards").fetchone()["n"],
        "messages": c.execute("SELECT COUNT(*) n FROM messages").fetchone()["n"],
        "active": sum(
            presence(r["last_seen_at"]) == "active"
            for r in c.execute("SELECT last_seen_at FROM agents")
        ),
        "cursor": cursor(),
    }
