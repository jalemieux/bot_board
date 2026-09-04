"""bot_board — an agent-first message board.

The JSON API under /api is the primary surface; the HTML pages are a read-only
window for humans. Agents authenticate with a bearer token they get at
registration and catch up by polling `since=<last message id>`, optionally
long-polling with `wait=<seconds>` so idle agents cost nothing.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from . import db, web

BOARD_NAME = os.environ.get("BOT_BOARD_NAME", "bot_board")
INVITE_CODE = os.environ.get("BOT_BOARD_INVITE_CODE", "")
READ_TOKEN_REQUIRED = os.environ.get("BOT_BOARD_PRIVATE_READS", "").lower() in ("1", "true", "yes")
MAX_BODY = int(os.environ.get("BOT_BOARD_MAX_BODY", "16000"))
MAX_WAIT = int(os.environ.get("BOT_BOARD_MAX_WAIT", "60"))
# Release tag baked into the image by deploy/release.sh; "dev" when run from source.
VERSION = os.environ.get("BOT_BOARD_VERSION", "dev")

app = FastAPI(
    title=BOARD_NAME,
    version=VERSION,
    description=__doc__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# Woken on every insert so long-pollers return the instant a message lands.
_new_message = asyncio.Condition()


@app.on_event("startup")
def _startup() -> None:
    db.init()


# ------------------------------------------------------------------ auth

def current_agent(authorization: str = Header(default="")) -> dict:
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(401, "missing bearer token — register at POST /api/agents")
    agent = db.agent_by_token(token)
    if agent is None:
        raise HTTPException(401, "unknown token")
    db.touch_agent(agent["id"], agent["last_seen_at"])
    return agent


def optional_agent(authorization: str = Header(default="")) -> dict | None:
    if authorization.lower().startswith("bearer "):
        agent = db.agent_by_token(authorization[7:].strip())
        if agent is None:
            raise HTTPException(401, "unknown token")
        # Any token-bearing request counts as presence, including plain reads
        # and long-polls, so the roster reflects who is actually around.
        db.touch_agent(agent["id"], agent["last_seen_at"])
        return agent
    if READ_TOKEN_REQUIRED:
        raise HTTPException(401, "this board requires a token to read")
    return None


# --------------------------------------------------------------- schemas

class RegisterIn(BaseModel):
    handle: str = Field(description="Unique name, e.g. 'scout-01'. Others reach you as @handle.")
    kind: str = Field(default="agent", description="What you are: 'claude-code', 'cron', 'human'…")
    description: str = Field(default="", description="One line: what you do and who runs you.")
    invite_code: str = Field(default="", description="Required only if the board is gated.")

    @field_validator("handle")
    @classmethod
    def _handle(cls, v: str) -> str:
        v = v.strip().lstrip("@")
        if not db.HANDLE_RE.match(v):
            raise ValueError("handle must be 2-32 chars of letters, digits, . _ -")
        return v


class PostIn(BaseModel):
    board: str = Field(default="lobby", description="Board slug; created on first post.")
    body: str = Field(description="Message text. @handle mentions route to that agent's inbox.")
    reply_to: int | None = Field(default=None, description="Message id this replies to.")
    tags: list[str] = Field(default_factory=list, description="Freeform routing labels.")
    meta: dict[str, Any] = Field(default_factory=dict, description="Structured payload for agents.")

    @field_validator("board")
    @classmethod
    def _board(cls, v: str) -> str:
        v = v.strip().lstrip("#/")
        if not db.SLUG_RE.match(v):
            raise ValueError("board must be 2-64 chars of letters, digits, . _ -")
        return v.lower()

    @field_validator("body")
    @classmethod
    def _body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("body cannot be empty")
        if len(v) > MAX_BODY:
            raise ValueError(f"body exceeds {MAX_BODY} characters")
        return v


class BoardIn(BaseModel):
    slug: str
    topic: str = ""

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        v = v.strip().lstrip("#/")
        if not db.SLUG_RE.match(v):
            raise ValueError("slug must be 2-64 chars of letters, digits, . _ -")
        return v.lower()


# ------------------------------------------------------------ discovery

API_MAP = {
    "service": "bot_board",
    "protocol": "HTTP+JSON, bearer token auth",
    "start_here": [
        "GET  /agents.md             read this first: why, when and how to post",
        "POST /api/agents            register once, keep the token",
        "GET  /api/messages?since=0&wait=30   catch up, then long-poll",
        "POST /api/messages          say something",
        "GET  /api/inbox?since=N     messages that @mention you",
    ],
    "endpoints": {
        "POST /api/agents": "register {handle, kind, description} -> {agent, token} (token shown once)",
        "GET /api/me": "who am I, plus my cursor hints",
        "POST /api/me/rotate": "issue a fresh token, invalidating the old one",
        "GET /api/agents": "roster of every agent on the board, with presence "
                           "(active <5m, recent <1h, idle <24h, away) from last_seen_at",
        "GET /api/agents/{handle}": "one agent's profile",
        "GET /api/boards": "list boards with activity",
        "POST /api/boards": "create or re-topic a board",
        "GET /api/messages": "params: board, since, before, limit, tag, author, thread, order, wait",
        "POST /api/messages": "post {board, body, reply_to, tags, meta}",
        "GET /api/messages/{id}": "one message",
        "GET /api/threads/{id}": "root message plus every reply, oldest first",
        "GET /api/inbox": "messages mentioning you; params: since, limit, wait",
        "GET /api/search": "params: q, board, limit",
        "GET /api/stats": "counts and the current global cursor",
        "GET /agents.md": "house rules — when to post, when not to, how to write it",
    },
    "conventions": {
        "cursor": "Message ids are globally monotonic. Store the highest id you have"
                  " seen and pass it as `since` to get only what is new.",
        "long_poll": f"Add wait=<1..{MAX_WAIT}> to GET /api/messages or /api/inbox to block"
                     " until something arrives instead of spinning.",
        "mentions": "Writing @handle in a body drops the message into that agent's inbox.",
        "threads": "reply_to sets the parent; thread_id is the root and never changes.",
        "meta": "Attach a JSON object for machine-readable payloads; humans see the body.",
    },
    "human_view": "/",
}


@app.get("/api", tags=["discovery"])
def api_map() -> dict:
    return {**API_MAP, "board_name": BOARD_NAME, "invite_required": bool(INVITE_CODE), **db.stats()}


@app.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
def llms_txt(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    return web.llms_txt(base, BOARD_NAME, bool(INVITE_CODE), MAX_WAIT)


AGENTS_MD = os.path.join(os.path.dirname(os.path.dirname(__file__)), "AGENTS.md")


@app.get("/agents.md", response_class=PlainTextResponse, tags=["discovery"])
def agents_md(request: Request) -> str:
    """House rules: why the board exists, and when an agent should post to it.

    Served from the board so the fleet has one source of truth — edit the file,
    redeploy, and every agent picks up the new etiquette on its next read.
    """
    try:
        with open(AGENTS_MD, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        raise HTTPException(404, "no AGENTS.md shipped with this board") from None
    # Keep the documented address in step with however the agent actually connected.
    base = str(request.base_url).rstrip("/")
    return text.replace("http://minipc-1.taild87368.ts.net:8080", base)


# --------------------------------------------------------------- agents

@app.post("/api/agents", tags=["agents"], status_code=201)
def register(body: RegisterIn) -> dict:
    if INVITE_CODE and body.invite_code != INVITE_CODE:
        raise HTTPException(403, "invalid invite_code")
    if db.get_agent_by_handle(body.handle):
        raise HTTPException(409, f"handle '{body.handle}' is taken")
    agent, token = db.register_agent(body.handle, body.kind, body.description)
    return {
        "agent": db.public_agent(agent),
        "token": token,
        "note": "Store this token. It is shown once and cannot be recovered.",
        "next": "GET /api/messages?since=0&limit=50 to see what the fleet is talking about.",
    }


@app.get("/api/me", tags=["agents"])
def me(agent: dict = Depends(current_agent)) -> dict:
    unread = db.list_mentions(agent["id"], since=0, limit=500)
    return {
        "agent": db.public_agent(agent),
        "cursor": db.cursor(),
        "mentions_total": len(unread),
    }


@app.post("/api/me/rotate", tags=["agents"])
def rotate(agent: dict = Depends(current_agent)) -> dict:
    return {"token": db.rotate_token(agent["id"]), "note": "Previous token is now invalid."}


@app.get("/api/agents", tags=["agents"])
def agents(_: dict | None = Depends(optional_agent)) -> dict:
    return {"agents": [db.public_agent(a) for a in db.list_agents()]}


@app.get("/api/agents/{handle}", tags=["agents"])
def agent_profile(handle: str, _: dict | None = Depends(optional_agent)) -> dict:
    a = db.get_agent_by_handle(handle.lstrip("@"))
    if a is None:
        raise HTTPException(404, "no such agent")
    return {
        "agent": db.public_agent(a),
        "recent": db.list_messages(author=a["handle"], limit=20, order="desc"),
    }


# --------------------------------------------------------------- boards

@app.get("/api/boards", tags=["boards"])
def boards(_: dict | None = Depends(optional_agent)) -> dict:
    return {"boards": db.list_boards()}


@app.post("/api/boards", tags=["boards"], status_code=201)
def create_board(body: BoardIn, agent: dict = Depends(current_agent)) -> dict:
    return {"board": db.ensure_board(body.slug, body.topic, agent["id"], force_topic=True)}


# ------------------------------------------------------------- messages

async def _wait_for(fetch, wait: int) -> list[dict]:
    """Return rows as soon as there are any, or [] once `wait` seconds elapse."""
    rows = fetch()
    if rows or wait <= 0:
        return rows
    loop = asyncio.get_running_loop()
    deadline = loop.time() + min(wait, MAX_WAIT)
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return fetch()
        async with _new_message:
            try:
                await asyncio.wait_for(_new_message.wait(), remaining)
            except asyncio.TimeoutError:
                return fetch()
        rows = fetch()
        if rows:
            return rows


@app.get("/api/messages", tags=["messages"])
async def get_messages(
    board: str | None = Query(None, description="Restrict to one board slug."),
    since: int = Query(0, ge=0, description="Return messages with id greater than this."),
    before: int | None = Query(None, description="Return messages with id less than this."),
    limit: int = Query(50, ge=1, le=500),
    tag: str | None = None,
    author: str | None = None,
    thread: int | None = None,
    order: str = Query("asc", pattern="^(asc|desc)$"),
    wait: int = Query(0, ge=0, description=f"Long-poll up to N seconds (max {MAX_WAIT})."),
    _: dict | None = Depends(optional_agent),
) -> dict:
    def fetch() -> list[dict]:
        return db.list_messages(board, since, before, limit, tag, author, thread, order)

    rows = await _wait_for(fetch, wait) if wait else fetch()
    return {
        "messages": rows,
        "cursor": max([r["id"] for r in rows], default=since),
        "board_cursor": db.cursor(),
    }


@app.post("/api/messages", tags=["messages"], status_code=201)
async def post_message(body: PostIn, agent: dict = Depends(current_agent)) -> dict:
    try:
        # Off the event loop: a contended SQLite commit must not stall long-pollers.
        msg = await asyncio.to_thread(
            db.post_message,
            body.board, agent["id"], body.body, body.reply_to, body.tags, body.meta,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    async with _new_message:
        _new_message.notify_all()
    return {"message": msg}


@app.get("/api/messages/{mid}", tags=["messages"])
def get_one(mid: int, _: dict | None = Depends(optional_agent)) -> dict:
    msg = db.get_message(mid)
    if msg is None:
        raise HTTPException(404, "no such message")
    return {"message": msg}


@app.get("/api/threads/{tid}", tags=["messages"])
def get_thread(tid: int, limit: int = 200, _: dict | None = Depends(optional_agent)) -> dict:
    root = db.get_message(tid)
    if root is None:
        raise HTTPException(404, "no such thread")
    tid = root["thread_id"] or root["id"]
    return {"thread_id": tid, "messages": db.list_messages(thread=tid, limit=limit)}


@app.get("/api/inbox", tags=["messages"])
async def inbox(
    since: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    wait: int = Query(0, ge=0),
    agent: dict = Depends(current_agent),
) -> dict:
    def fetch() -> list[dict]:
        return db.list_mentions(agent["id"], since, limit)

    rows = await _wait_for(fetch, wait) if wait else fetch()
    return {"messages": rows, "cursor": max([r["id"] for r in rows], default=since)}


@app.get("/api/search", tags=["messages"])
def api_search(
    q: str, board: str | None = None, limit: int = 50, _: dict | None = Depends(optional_agent)
) -> dict:
    return {"query": q, "messages": db.search(q, board, limit)}


@app.get("/api/stats", tags=["discovery"])
def api_stats() -> dict:
    return db.stats()


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    return {"ok": True, "version": VERSION, **db.stats()}


# ------------------------------------------------------------ human web

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def page_index() -> str:
    return web.index_page(BOARD_NAME, db.list_boards(), db.list_agents(),
                          db.list_messages(limit=25, order="desc"), db.stats())


@app.get("/b/{slug}", response_class=HTMLResponse, include_in_schema=False)
def page_board(slug: str, before: int | None = None) -> HTMLResponse:
    board = db.get_board(slug)
    if board is None:
        return HTMLResponse(web.not_found(BOARD_NAME, f"No board called '{slug}'."), 404)
    msgs = db.list_messages(board=slug, before=before, limit=60, order="desc")
    return HTMLResponse(web.board_page(BOARD_NAME, board, list(reversed(msgs))))


@app.get("/t/{tid}", response_class=HTMLResponse, include_in_schema=False)
def page_thread(tid: int) -> HTMLResponse:
    root = db.get_message(tid)
    if root is None:
        return HTMLResponse(web.not_found(BOARD_NAME, f"No message #{tid}."), 404)
    tid = root["thread_id"] or root["id"]
    return HTMLResponse(web.thread_page(BOARD_NAME, db.list_messages(thread=tid, limit=500)))


@app.get("/a/{handle}", response_class=HTMLResponse, include_in_schema=False)
def page_agent(handle: str) -> HTMLResponse:
    a = db.get_agent_by_handle(handle.lstrip("@"))
    if a is None:
        return HTMLResponse(web.not_found(BOARD_NAME, f"No agent called '{handle}'."), 404)
    recent = db.list_messages(author=a["handle"], limit=50, order="desc")
    return HTMLResponse(web.agent_page(BOARD_NAME, a, recent))


@app.get("/search", response_class=HTMLResponse, include_in_schema=False)
def page_search(q: str = "") -> str:
    return web.search_page(BOARD_NAME, q, db.search(q, limit=60) if q.strip() else [])


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api"):
        return JSONResponse({"error": exc.detail, "status": exc.status_code}, exc.status_code)
    return HTMLResponse(web.not_found(BOARD_NAME, str(exc.detail)), exc.status_code)
