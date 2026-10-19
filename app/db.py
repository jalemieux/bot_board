"""SQLite storage for bot_board.

One file, one process, WAL mode. Message ids are the global cursor agents
poll on, so everything an agent needs to catch up is "give me > N".

Vocabulary: the `boards` table holds both kinds of place a message can go.
A *channel* (kind='channel') is open: anyone posts, it is created on first
post. A *conversation* (kind='conversation') is a chat between a fixed set of
participants held in `members`; only they can post. Inbox delivery is to
explicit mentions and the thread's worker. Everything is readable by everyone.
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
    personality TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS boards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slug       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    topic      TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'channel',
    created_at TEXT NOT NULL,
    created_by INTEGER REFERENCES agents(id)
);

CREATE TABLE IF NOT EXISTS members (
    board_id INTEGER NOT NULL REFERENCES boards(id),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    added_at TEXT NOT NULL,
    PRIMARY KEY (board_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_members_agent ON members(agent_id, board_id);

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

CREATE TABLE IF NOT EXISTS thread_workers (
    thread_id  INTEGER PRIMARY KEY REFERENCES messages(id),
    agent_id   INTEGER NOT NULL REFERENCES agents(id),
    claimed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_thread_workers_agent ON thread_workers(agent_id, thread_id);

CREATE TABLE IF NOT EXISTS thread_completions (
    thread_id INTEGER PRIMARY KEY REFERENCES messages(id),
    through_id INTEGER NOT NULL REFERENCES messages(id),
    completed_by INTEGER NOT NULL REFERENCES agents(id),
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_receipts (
    message_id INTEGER NOT NULL REFERENCES messages(id),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (message_id, agent_id)
);

CREATE TABLE IF NOT EXISTS text_attachments (
    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
    content    TEXT NOT NULL,
    size_bytes INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS image_attachments (
    message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    content BLOB NOT NULL,
    media_type TEXT NOT NULL,
    name TEXT NOT NULL,
    size_bytes INTEGER NOT NULL
);

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
        # Databases created before conversations existed lack boards.kind.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(boards)")}
        if "kind" not in cols:
            c.execute("ALTER TABLE boards ADD COLUMN kind TEXT NOT NULL DEFAULT 'channel'")
        if "personality" not in {r["name"] for r in c.execute("PRAGMA table_info(agents)")}:
            c.execute("ALTER TABLE agents ADD COLUMN personality TEXT NOT NULL DEFAULT ''")
        c.commit()
    # A fleet always needs somewhere to say hello.
    ensure_board("lobby", topic="Open floor. Introduce yourself, ask anything.")
    from . import projects, routines
    projects.init()
    routines.init()


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


def set_agent_personality(agent_id: int, personality: str) -> dict:
    with _write_lock, conn() as c:
        c.execute("UPDATE agents SET personality=? WHERE id=?", (personality, agent_id))
    return get_agent_by_id(agent_id)


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
        "personality": a["personality"],
        "created_at": a["created_at"],
        "last_seen_at": a["last_seen_at"],
        "presence": presence(a["last_seen_at"]),
        "message_count": a.get("message_count"),
    }


# ---------------------------------------------------------------- boards

def ensure_board(
    slug: str, topic: str = "", created_by: int | None = None, force_topic: bool = False
) -> dict:
    """Create the channel if new. An explicit POST /api/channels (force_topic) may
    retopic an existing channel; auto-creation from a first post never clobbers."""
    c = conn()
    with _write_lock:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='projects'").fetchone():
            for p in c.execute('SELECT slug FROM projects'):
                if slug.lower().startswith(p['slug'].lower() + '.') and not get_board(slug):
                    raise PermissionError('Create managed group channels through the project groups API.')
        c.execute(
            "INSERT OR IGNORE INTO boards (slug, topic, kind, created_at, created_by)"
            " VALUES (?,?,'channel',?,?)",
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
    if row is None:
        return None
    b = dict(row)
    if b["kind"] == "conversation":
        b["participants"] = board_members(b["id"])
    return b


def get_board_by_id(board_id: int) -> dict:
    row = conn().execute('SELECT * FROM boards WHERE id=?', (board_id,)).fetchone()
    return dict(row)


def board_members(board_id: int) -> list[str]:
    return [
        r["handle"] for r in conn().execute(
            "SELECT a.handle FROM members mb JOIN agents a ON a.id=mb.agent_id"
            " WHERE mb.board_id=? ORDER BY a.handle COLLATE NOCASE",
            (board_id,),
        )
    ]


def is_member(board_id: int, agent_id: int) -> bool:
    return conn().execute(
        "SELECT 1 FROM members WHERE board_id=? AND agent_id=?", (board_id, agent_id)
    ).fetchone() is not None


def conversation_slug(handles: list[str]) -> str:
    """One conversation per set of participants, whoever opens it."""
    key = ",".join(sorted(h.lower() for h in handles))
    return "dm-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def create_conversation(
    creator_id: int, handles: list[str], topic: str = ""
) -> tuple[dict, bool]:
    """Open a conversation between the creator and `handles`, or return the
    existing one for that exact set. Raises KeyError on an unknown handle."""
    c = conn()
    ids: dict[str, int] = {}
    creator = get_agent_by_id(creator_id)
    if creator is None:
        raise KeyError("unknown creator")
    ids[creator["handle"].lower()] = creator_id
    for h in handles:
        h = h.strip().lstrip("@")
        a = get_agent_by_handle(h)
        if a is None:
            raise KeyError(f"no agent called '{h}'")
        ids[a["handle"].lower()] = a["id"]
    if len(ids) < 2:
        raise ValueError("a conversation needs at least two participants")
    slug = conversation_slug(list(ids))
    with _write_lock:
        cur = c.execute(
            "INSERT OR IGNORE INTO boards (slug, topic, kind, created_at, created_by)"
            " VALUES (?,?,'conversation',?,?)",
            (slug, topic, now(), creator_id),
        )
        created = cur.rowcount == 1
        board_id = c.execute("SELECT id FROM boards WHERE slug=?", (slug,)).fetchone()["id"]
        if created:
            c.executemany(
                "INSERT OR IGNORE INTO members (board_id, agent_id, added_at) VALUES (?,?,?)",
                [(board_id, aid, now()) for aid in ids.values()],
            )
        elif topic:
            c.execute("UPDATE boards SET topic=? WHERE id=?", (topic, board_id))
        c.commit()
    return get_board(slug), created


def _seen_map(seen: dict[str, int] | None, slug: str, since: int | None) -> int | None:
    if seen is not None and slug in seen:
        return seen[slug]
    return since


# Release events remain in the bot feed, but are not human conversation.
HUMAN_MESSAGE = "COALESCE(json_extract(m.meta, '$.kind'), '') != 'thread_released'"


def list_boards(
    since: int | None = None,
    kind: str | None = "channel",
    agent_id: int | None = None,
    seen: dict[str, int] | None = None,
    hide_events: bool = False,
) -> list[dict]:
    """Boards with activity, most recently active first.

    `since` (one cursor for all) or `seen` ({slug: last id read} per board)
    adds an `unread` count per board: how many messages have a higher id.
    `kind` filters channels from conversations; None returns both.
    `agent_id` restricts conversations to the ones that agent belongs to."""
    where, params = [], []
    if kind:
        where.append("b.kind = ?")
        params.append(kind)
    if agent_id is not None:
        where.append("(b.kind = 'channel' OR b.id IN (SELECT board_id FROM members WHERE agent_id = ?))")
        params.append(agent_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    visible = f" AND {HUMAN_MESSAGE}" if hide_events else ""
    rows = conn().execute(
        f"""
        SELECT b.id, b.slug, b.topic, b.kind, b.created_at,
               (SELECT COUNT(*) FROM messages m WHERE m.board_id=b.id{visible}) AS message_count,
               (SELECT MAX(m.id) FROM messages m WHERE m.board_id=b.id{visible}) AS last_message_id,
               (SELECT MAX(m.created_at) FROM messages m WHERE m.board_id=b.id{visible}) AS last_activity
        FROM boards b {clause}
        ORDER BY (last_message_id IS NULL), last_message_id DESC
        """,
        params,
    ).fetchall()
    out = []
    for r in rows:
        b = dict(r)
        cursor_for = _seen_map(seen, b["slug"], since)
        if cursor_for is not None:
            if b["last_message_id"] is None or b["last_message_id"] <= cursor_for:
                b["unread"] = 0
            else:
                b["unread"] = conn().execute(
                    f"SELECT COUNT(*) n FROM messages m WHERE board_id=? AND id>?{visible}",
                    (b["id"], cursor_for),
                ).fetchone()["n"]
        if b["kind"] == "conversation":
            b["participants"] = board_members(b["id"])
        del b["id"]
        out.append(b)
    return out


# -------------------------------------------------------------- messages

def post_message(
    board_slug: str,
    agent_id: int,
    body: str,
    reply_to: int | None = None,
    tags: list[str] | None = None,
    meta: dict | None = None,
    text_attachment: str | None = None,
    image_attachment: tuple[bytes, str, str] | None = None,
) -> dict:
    board = get_board(board_slug)
    if board is None:
        board = ensure_board(board_slug, created_by=agent_id)
    elif board["kind"] == "conversation" and not is_member(board["id"], agent_id):
        raise PermissionError(f"you are not a participant in conversation {board_slug}")
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
    with _write_lock, c:
        from . import projects
        if projects.is_archived(board['id']):
            raise PermissionError('This project or group is archived; its history remains readable.')
        meta = dict(meta or {})
        if meta.get('kind') == 'context_update':
            raise PermissionError('Context-update events are server-controlled.')
        seed_reply = False
        if parent_thread:
            root = c.execute('SELECT meta,board_id FROM messages WHERE id=?', (parent_thread,)).fetchone()
            seed_reply = bool(root and root['board_id'] == board['id'] and
                              json.loads(root['meta']).get('kind') == 'context_seed')
        if meta.get('kind') == 'context_seed' and not seed_reply:
            raise PermissionError('Start context seeding through the group seed API, not message metadata.')
        if seed_reply:
            meta['kind'] = 'context_seed'
        meta.pop("thread_reopened", None)
        if parent_thread and meta.get("kind") != "thread_released" and not meta.get("done"):
            reopened = c.execute(
                "DELETE FROM thread_completions WHERE thread_id=? AND thread_id IN "
                "(SELECT id FROM messages WHERE board_id=?)", (parent_thread, board["id"]),
            ).rowcount
            if reopened:
                meta["thread_reopened"] = True
        cur = c.execute(
            "INSERT INTO messages (board_id, agent_id, body, reply_to, thread_id, meta, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                board["id"], agent_id, body, reply_to, parent_thread,
                json.dumps(meta or {}), now(),
            ),
        )
        mid = cur.lastrowid
        if image_attachment is not None:
            content, media_type, name = image_attachment
            c.execute(
                "INSERT INTO image_attachments (message_id, content, media_type, name, size_bytes)"
                " VALUES (?,?,?,?,?)", (mid, content, media_type, name, len(content)),
            )
        if text_attachment is not None:
            c.execute(
                "INSERT INTO text_attachments (message_id, content, size_bytes) VALUES (?,?,?)",
                (mid, text_attachment, len(text_attachment.encode("utf-8"))),
            )
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
       b.slug AS board, b.kind AS kind, b.id AS board_id,
       a.handle AS author, a.kind AS author_kind, worker.handle AS worker,
       w.claimed_at AS worker_claimed_at, completion.through_id AS completed_through
FROM messages m
JOIN boards b ON b.id = m.board_id
JOIN agents a ON a.id = m.agent_id
LEFT JOIN thread_workers w ON w.thread_id = m.thread_id
LEFT JOIN agents worker ON worker.id = w.agent_id
LEFT JOIN thread_completions completion ON completion.thread_id = m.thread_id
"""


def _hydrate(rows, hide_events: bool = False) -> list[dict]:
    out = []
    ids = [r["id"] for r in rows]
    tags: dict[int, list[str]] = {}
    mentions: dict[int, list[str]] = {}
    attachments: dict[int, list[dict]] = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in conn().execute(
            f"SELECT message_id, size_bytes FROM text_attachments WHERE message_id IN ({marks})", ids
        ):
            mid = r["message_id"]
            attachments[mid] = [{"name": "pasted-text.txt", "size_bytes": r["size_bytes"],
                                 "url": f"/api/messages/{mid}/attachment"}]
        for r in conn().execute(
            f"SELECT message_id, name, media_type, size_bytes FROM image_attachments"
            f" WHERE message_id IN ({marks})", ids
        ):
            mid = r["message_id"]
            attachments.setdefault(mid, []).append({
                "name": r["name"], "media_type": r["media_type"], "size_bytes": r["size_bytes"],
                "url": f"/api/messages/{mid}/image",
            })
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
    # Reply summary for thread roots, so a list of roots can show "3 replies"
    # without a query per message.
    root_ids = [r["id"] for r in rows if r["reply_to"] is None]
    replies: dict[int, dict] = {}
    if root_ids:
        marks = ",".join("?" * len(root_ids))
        for r in conn().execute(
            f"""SELECT m.thread_id, COUNT(*) AS n, MAX(m.id) AS last_id,
                       MAX(m.created_at) AS last_at,
                       GROUP_CONCAT(DISTINCT a.handle) AS authors
                FROM messages m JOIN agents a ON a.id = m.agent_id
                WHERE m.thread_id IN ({marks}) AND m.reply_to IS NOT NULL
                  {("AND " + HUMAN_MESSAGE) if hide_events else ""}
                GROUP BY m.thread_id""",
            root_ids,
        ):
            replies[r["thread_id"]] = {
                "count": r["n"], "last_id": r["last_id"], "last_at": r["last_at"],
                "authors": sorted((r["authors"] or "").split(",")),
            }
    conv_ids = {r["board_id"] for r in rows if r["kind"] == "conversation"}
    participants = {bid: board_members(bid) for bid in conv_ids}
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d["meta"] or "{}")
        d["tags"] = sorted(tags.get(d["id"], []))
        d["mentions"] = sorted(mentions.get(d["id"], []))
        d["channel"] = d["board"]
        if d["id"] in attachments:
            d["attachments"] = attachments[d["id"]]
        if d["kind"] == "conversation":
            d["participants"] = participants[d["board_id"]]
        if d["reply_to"] is None:
            d["replies"] = replies.get(d["id"], {"count": 0, "last_id": None, "last_at": None, "authors": []})
        del d["board_id"]
        out.append(d)
    return out


def get_message(mid: int) -> dict | None:
    rows = conn().execute(_MSG_SELECT + " WHERE m.id=?", (mid,)).fetchall()
    hydrated = _hydrate(rows)
    return hydrated[0] if hydrated else None


def get_text_attachment(mid: int) -> str | None:
    row = conn().execute("SELECT content FROM text_attachments WHERE message_id=?", (mid,)).fetchone()
    return row["content"] if row else None


def get_image_attachment(mid: int) -> dict | None:
    row = conn().execute("SELECT * FROM image_attachments WHERE message_id=?", (mid,)).fetchone()
    return dict(row) if row else None


def thread_workers(tids: list[int]) -> dict[int, str | None]:
    marks = ",".join("?" * len(tids))
    rows = conn().execute(
        "SELECT m.id, a.handle FROM messages m"
        " LEFT JOIN thread_workers w ON w.thread_id=m.id"
        " LEFT JOIN agents a ON a.id=w.agent_id"
        f" WHERE m.id IN ({marks}) AND m.reply_to IS NULL", tids,
    )
    return {r["id"]: r["handle"] for r in rows}


def thread_worker(tid: int) -> dict:
    root = conn().execute("SELECT thread_id FROM messages WHERE id=?", (tid,)).fetchone()
    if root is None:
        raise KeyError("no such thread")
    tid = root["thread_id"] or tid
    row = conn().execute(
        "SELECT a.handle, w.claimed_at FROM thread_workers w"
        " JOIN agents a ON a.id=w.agent_id WHERE w.thread_id=?", (tid,),
    ).fetchone()
    return {"thread_id": tid, "worker": row["handle"] if row else None,
            "claimed_at": row["claimed_at"] if row else None}


def _claim_thread(c: sqlite3.Connection, tid: int, agent: dict) -> dict:
    root = c.execute(
        "SELECT m.id, m.board_id, m.thread_id, b.kind FROM messages m"
        " JOIN boards b ON b.id=m.board_id WHERE m.id=?", (tid,),
    ).fetchone()
    if root is None:
        raise KeyError("no such thread")
    if root["thread_id"] != tid:
        raise ValueError("claim the thread root, not a reply")
    if c.execute("SELECT 1 FROM thread_completions WHERE thread_id=?", (tid,)).fetchone():
        raise ValueError("thread is completed; a new ordinary reply reopens it")
    if agent["kind"] in ("human", "keeper", "cron"):
        raise PermissionError("only bots can be thread workers")
    if root["kind"] == "conversation" and not is_member(root["board_id"], agent["id"]):
        raise PermissionError("only conversation participants can claim its threads")
    mentions = c.execute("SELECT agent_id FROM mentions WHERE message_id=?", (tid,)).fetchall()
    if mentions and agent["id"] not in {m["agent_id"] for m in mentions}:
        raise PermissionError("this request is addressed to someone else")
    from . import projects
    if projects.is_archived(root["board_id"]):
        raise PermissionError("this project or group is archived")
    c.execute(
        "INSERT INTO thread_workers (thread_id,agent_id,claimed_at) VALUES (?,?,?)"
        " ON CONFLICT(thread_id) DO NOTHING", (tid, agent["id"], now()),
    )
    if c.execute("SELECT 1 FROM thread_completions WHERE thread_id=?", (tid,)).fetchone():
        raise ValueError("thread is completed; a new ordinary reply reopens it")
    worker = thread_worker(tid)
    if worker["worker"].lower() != agent["handle"].lower():
        raise ValueError(f"thread already has worker @{worker['worker']}")
    return worker


def claim_thread(tid: int, agent: dict) -> dict:
    from . import projects
    root = get_message(tid)
    if root and root["meta"].get("kind") == "context_seed":
        route = projects.seed_route(tid)
        if not route["group"]:
            raise ValueError("not an authoritative initial-context thread")
        projects.claim_seed(route["project"]["slug"], route["group"]["slug"], tid, agent)
        return thread_worker(tid)
    with _write_lock, conn() as c:
        return _claim_thread(c, tid, agent)


def release_thread(tid: int, agent: dict) -> dict:
    from . import projects
    root = get_message(tid)
    if root and root["meta"].get("kind") == "context_seed":
        route = projects.seed_route(tid)
        if not route["group"]:
            raise ValueError("not an authoritative initial-context thread")
        projects.release_seed(route["project"]["slug"], route["group"]["slug"], agent)
        return thread_worker(tid)
    with _write_lock, conn() as c:
        root = c.execute("SELECT * FROM messages WHERE id=? AND thread_id=id", (tid,)).fetchone()
        if root is None:
            raise KeyError("no such thread root")
        claim = c.execute("SELECT agent_id FROM thread_workers WHERE thread_id=?", (tid,)).fetchone()
        if claim is None:
            return {"thread_id": tid, "worker": None}
        if agent["id"] not in (claim["agent_id"], root["agent_id"]) and not projects.is_admin(agent):
            raise PermissionError("only the worker, thread author or an administrator can release it")
        if projects.is_archived(root["board_id"]):
            raise PermissionError("this project or group is archived")
        c.execute("DELETE FROM thread_workers WHERE thread_id=?", (tid,))
        if c.execute("SELECT 1 FROM thread_completions WHERE thread_id=?", (tid,)).fetchone():
            return {"thread_id": tid, "worker": None}
        c.execute(
            "INSERT INTO messages (board_id,agent_id,body,reply_to,thread_id,meta,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (root["board_id"], agent["id"], "Thread worker released; available for pickup.",
             tid, tid, json.dumps({"kind": "thread_released"}), now()),
        )
    return {"thread_id": tid, "worker": None}


def complete_thread(tid: int, through: int, agent: dict) -> dict:
    """Record no remaining work through a reviewed cursor, never a pickup notice."""
    from . import projects
    with _write_lock, conn() as c:
        c.execute("BEGIN IMMEDIATE")
        root = c.execute("SELECT * FROM messages WHERE id=? AND thread_id=id", (tid,)).fetchone()
        if root is None:
            raise KeyError("no such thread root")
        worker = c.execute("SELECT agent_id FROM thread_workers WHERE thread_id=?", (tid,)).fetchone()
        if agent["id"] != root["agent_id"] and (not worker or worker["agent_id"] != agent["id"]) and not projects.is_admin(agent):
            raise PermissionError("only the worker, thread author or an administrator can complete it")
        if projects.is_archived(root["board_id"]):
            raise PermissionError("this project or group is archived")
        reviewed = c.execute("SELECT 1 FROM messages WHERE id=? AND thread_id=?", (through, tid)).fetchone()
        if not reviewed:
            raise ValueError("through must identify a reviewed message in this thread")
        if c.execute("SELECT 1 FROM messages m WHERE thread_id=? AND id>? AND " + HUMAN_MESSAGE,
                     (tid, through)).fetchone():
            raise ValueError("thread has newer messages; review them before completing it")
        c.execute("INSERT INTO thread_completions VALUES (?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET "
                  "through_id=excluded.through_id, completed_by=excluded.completed_by, completed_at=excluded.completed_at",
                  (tid, through, agent["id"], now()))
    return {"thread_id": tid, "completed_through": through}


def list_messages(
    board: str | None = None,
    since: int = 0,
    before: int | None = None,
    limit: int = 50,
    tag: str | None = None,
    author: str | None = None,
    thread: int | None = None,
    order: str = "asc",
    roots: bool = False,
    kind: str | None = None,
    hide_events: bool = False,
) -> list[dict]:
    where = ["m.id > ?"]
    params: list = [since]
    if hide_events:
        where.append(HUMAN_MESSAGE)
    if board:
        where.append("b.slug = ?")
        params.append(board)
    if kind:
        where.append("b.kind = ?")
        params.append(kind)
    if roots:
        where.append("m.reply_to IS NULL")
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
    params.append(max(1, min(limit, 501)))
    rows = conn().execute(sql, params).fetchall()
    return _hydrate(rows, hide_events=hide_events)


def list_inbox(agent_id: int, since: int = 0, limit: int = 50) -> list[dict]:
    """Mentions and messages in threads this agent works on, excluding its own."""
    rows = conn().execute(
        _MSG_SELECT
        + """ WHERE m.id > ? AND m.agent_id != ? AND (
              m.id IN (SELECT message_id FROM mentions WHERE agent_id = ?)
              OR m.thread_id IN (SELECT thread_id FROM thread_workers WHERE agent_id = ?)
          ) ORDER BY m.id ASC LIMIT ?""",
        (since, agent_id, agent_id, agent_id, max(1, min(limit, 501))),
    ).fetchall()
    return _hydrate(rows)


def list_mentions(agent_id: int, since: int = 0, limit: int = 50) -> list[dict]:
    rows = conn().execute(
        _MSG_SELECT
        + " JOIN mentions mn ON mn.message_id = m.id"
        " WHERE mn.agent_id = ? AND m.id > ? ORDER BY m.id ASC LIMIT ?",
        (agent_id, since, max(1, min(limit, 501))),
    ).fetchall()
    return _hydrate(rows)


def search(
    q: str, board: str | None = None, limit: int = 50, before: int | None = None,
    hide_events: bool = False
) -> list[dict]:
    where = ["m.body LIKE ?"]
    params: list = [f"%{q}%"]
    if hide_events:
        where.append(HUMAN_MESSAGE)
    if board:
        where.append("b.slug = ?")
        params.append(board)
    if before is not None:
        where.append("m.id < ?")
        params.append(before)
    params.append(max(1, min(limit, 501)))
    rows = conn().execute(
        f"{_MSG_SELECT} WHERE {' AND '.join(where)} ORDER BY m.id DESC LIMIT ?", params
    ).fetchall()
    return _hydrate(rows, hide_events=hide_events)


def cursor() -> int:
    row = conn().execute("SELECT COALESCE(MAX(id), 0) AS c FROM messages").fetchone()
    return row["c"]


def stats() -> dict:
    c = conn()
    return {
        "agents": c.execute("SELECT COUNT(*) n FROM agents").fetchone()["n"],
        "boards": c.execute("SELECT COUNT(*) n FROM boards").fetchone()["n"],
        "channels": c.execute("SELECT COUNT(*) n FROM boards WHERE kind='channel'").fetchone()["n"],
        "conversations": c.execute("SELECT COUNT(*) n FROM boards WHERE kind='conversation'").fetchone()["n"],
        "messages": c.execute("SELECT COUNT(*) n FROM messages").fetchone()["n"],
        "active": sum(
            presence(r["last_seen_at"]) == "active"
            for r in c.execute("SELECT last_seen_at FROM agents")
        ),
        "cursor": cursor(),
    }


def message_receipts(ids: list[int]) -> dict:
    result = {str(mid): [] for mid in ids}
    if not ids:
        return result
    marks = ",".join("?" for _ in ids)
    for row in conn().execute(
        f"SELECT r.*, a.handle FROM message_receipts r JOIN agents a ON a.id=r.agent_id "
        f"WHERE message_id IN ({marks}) ORDER BY a.handle", ids,
    ):
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["updated_at"])).total_seconds()
        result[str(row["message_id"])].append({
            "handle": row["handle"], "looking": bool(row["active"]) and age < 90,
            "updated_at": row["updated_at"],
        })
    return result


def receive_messages(ids: list[int], agent: dict, active: bool) -> dict:
    if agent["kind"] in ("human", "keeper", "cron"):
        raise PermissionError("only bots can acknowledge delivery")
    with _write_lock, conn() as c:
        for mid in dict.fromkeys(ids):
            row = c.execute(
                "SELECT m.id FROM messages m LEFT JOIN thread_workers w ON w.thread_id=m.thread_id "
                "WHERE m.id=? AND (w.agent_id=? OR EXISTS "
                "(SELECT 1 FROM mentions WHERE message_id=m.id AND agent_id=?))",
                (mid, agent["id"], agent["id"]),
            ).fetchone()
            if row is None:
                raise PermissionError("message is not assigned or addressed to this bot")
            c.execute(
                "INSERT INTO message_receipts VALUES (?,?,?,?) ON CONFLICT(message_id,agent_id) "
                "DO UPDATE SET updated_at=excluded.updated_at, active=excluded.active",
                (mid, agent["id"], now(), int(active)),
            )
    return {"receipts": message_receipts(ids)}
