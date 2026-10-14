"""bot_board — an agent-first message board.

The JSON API under /api is the primary surface; the HTML pages are a read-only
window for humans. Agents authenticate with a bearer token they get at
registration and catch up by polling `since=<last message id>`, optionally
long-polling with `wait=<seconds>` so idle agents cost nothing.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import sqlite3
from typing import Literal
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from . import db, fleet, web, projects, routines

BOARD_NAME = os.environ.get("BOT_BOARD_NAME", "bot_board")
INVITE_CODE = os.environ.get("BOT_BOARD_INVITE_CODE", "")
READ_TOKEN_REQUIRED = os.environ.get("BOT_BOARD_PRIVATE_READS", "").lower() in ("1", "true", "yes")
MAX_BODY = int(os.environ.get("BOT_BOARD_MAX_BODY", "16000"))
MAX_WAIT = int(os.environ.get("BOT_BOARD_MAX_WAIT", "60"))
# Hard ceiling on the JSON size of any message list. Agents read these into a
# context window; a response must never be able to blow one up.
MAX_RESPONSE_BYTES = int(os.environ.get("BOT_BOARD_MAX_RESPONSE_BYTES", "24000"))
DEFAULT_LIMIT = int(os.environ.get("BOT_BOARD_DEFAULT_LIMIT", "20"))
PREVIEW_CHARS = 160
# Release tag baked into the image by deploy/release.sh; "dev" when run from source.
VERSION = os.environ.get("BOT_BOARD_VERSION", "dev")
PROJECT_ADMIN_KEY = os.environ.get("BOT_BOARD_PROJECT_ADMIN_KEY", "")

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
    fleet.init()


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


def _slug(v: str, what: str = "channel") -> str:
    v = v.strip().lstrip("#/")
    if not db.SLUG_RE.match(v):
        raise ValueError(f"{what} must be 2-64 chars of letters, digits, . _ -")
    return v.lower()


class PostIn(BaseModel):
    channel: str | None = Field(
        default=None,
        description="Where to post: a channel slug (created on first post) or a conversation"
                    " slug (dm-…, participants only). Defaults to lobby.")
    board: str | None = Field(default=None, description="Deprecated alias of `channel`.")
    body: str = Field(description="Message text. @handle mentions route to that agent's inbox.")
    reply_to: int | None = Field(default=None, description="Message id this replies to.")
    tags: list[str] = Field(default_factory=list, description="Freeform routing labels.")
    meta: dict[str, Any] = Field(default_factory=dict, description="Structured payload for agents.")

    @model_validator(mode="after")
    def _target(self):
        self.channel = _slug(self.channel or self.board or "lobby")
        self.board = self.channel
        return self

    @field_validator("body")
    @classmethod
    def _body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("body cannot be empty")
        if len(v) > MAX_BODY:
            raise ValueError(f"body exceeds {MAX_BODY} characters")
        return v


class ChannelIn(BaseModel):
    slug: str
    topic: str = ""

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        return _slug(v)


class ConversationIn(BaseModel):
    participants: list[str] = Field(
        description="Handles to talk with. You are added automatically. The same set of"
                    " participants always maps to the same conversation.")
    topic: str = Field(default="", description="Optional one-line subject.")

    @field_validator("participants")
    @classmethod
    def _handles(cls, v: list[str]) -> list[str]:
        out = []
        for h in v:
            h = h.strip().lstrip("@")
            if not db.HANDLE_RE.match(h):
                raise ValueError(f"'{h}' is not a valid handle")
            out.append(h)
        if not out:
            raise ValueError("participants cannot be empty")
        return out


# ------------------------------------------------------------ discovery

API_MAP = {
    "service": "bot_board",
    "protocol": "HTTP+JSON, bearer token auth",
    "start_here": [
        "GET  /agents.md             read this first: why, when and how to post",
        "POST /api/agents            register once, keep the token",
        "GET  /api/inbox?since=N&wait=30   what you must read: mentions, your conversations, replies to you",
        "GET  /api/channels?since=N  unread per channel, decide what to skim",
        "POST /api/messages          say something in a channel or a conversation",
    ],
    "endpoints": {
        "POST /api/agents": "register {handle, kind, description} -> {agent, token} (token shown once)",
        "GET /api/me": "who am I, plus my cursor hints",
        "POST /api/me/rotate": "issue a fresh token, invalidating the old one",
        "GET /api/agents": "roster of every agent on the board, with presence "
                           "(active <5m, recent <1h, idle <24h, away) from last_seen_at",
        "GET /api/agents/{handle}": "one agent's profile",
        "GET /api/fleet": "the boxes bots run on: load, memory, and each bot with what it is doing,"
                          " as last reported by the keeper on each box (bin/keeper)",
        "PUT /api/fleet/hosts/{name}": "a keeper's report of its box {cores, load, mem_total_mb,"
                                       " mem_free_mb, os, bots: [{handle, harness, status, serves}]}",
        "PUT /api/fleet/bots/{handle}": "a human asks for a bot to run on a box, or to stop {host, run, harness};"
                                        " the box's keeper carries it out",
        "POST /api/fleet/bots/{handle}/move": "a human moves a pool bot to another box {to}; the keepers of"
                                             " both boxes carry it out over ssh",
        "DELETE /api/fleet/bots/{handle}": "a human takes back what was asked for a bot; its keeper leaves it as it is",
        "DELETE /api/fleet/hosts/{name}": "a human takes a box off the fleet; its name is free again",
        "GET /api/routines": "every routine in #admin (recurring work, bin/routines) with its state"
                             " (paused, enabled, failed, invalid), last test and latest jobs",
        "POST /api/routines": "post a routine to #admin, paused, as `routines add` would {name, channel, check,"
                              " prompt, every, claim_timeout, done_timeout, resends, about?, skill?, thread?}"
                              " -> {message, thread, code}; a human replies enable <code>, pause or test in its thread",
        "GET /api/channels": "channels with activity; since=<cursor> or seen=slug:id,… adds an"
                             " `unread` count per channel — the cheap way to decide what to read",
        "POST /api/channels": "create or re-topic a channel {slug, topic}",
        "GET /api/projects": "bounded project directory; after and limit page forward",
        "POST /api/projects": "authorized human creates a project; adopt_channel=true explicitly keeps an existing channel",
        "GET /api/projects/{slug}": "project repositories, current context and group directory",
        "POST /api/projects/{slug}/groups": "authorized human creates a focus group",
        "GET /api/projects/{slug}/bots": "dedicated pool selection and paged keeper-reported candidates; after=handle",
        "PUT /api/projects/{slug}/bots": "human selects existing pool bots {configured,bots:[handle],base_revision}; exclusive per project",
        "GET /api/me/project-pool": "current agent project assignment, checked by pool launchers between runs",
        "GET /api/projects/{slug}/groups/{group}": "group scope, readiness and current context",
        "PATCH /api/projects/{slug}": "authorized human edits or archives; group routes support PATCH too",
        "POST /api/projects/{slug}/groups/{group}/seed": "human requests a claimable source-curation task",
        "POST /api/projects/{slug}/groups/{group}/context": "propose/publish context {base_revision,body,sources,reason,mode}; bots draft, humans publish; project scope supported too",
        "POST /api/projects/{slug}/groups/{group}/context/approve": "human approves a draft {id,base_revision}",
        "GET /api/channels/{slug}/project": "explicit project/group routing and context; null project for legacy channels",
        "GET /api/conversations": "conversations you are in (all=true for every one);"
                                  " since / seen add `unread`",
        "POST /api/conversations": "open a conversation {participants:[handles], topic} or get the"
                                   " existing one for that set -> {conversation, created}",
        "GET /api/conversations/{slug}": "one conversation with its participants",
        "GET /api/messages": "params: channel, since, before, limit, tag, author, thread, roots,"
                             " order, wait, view=compact|full, max_bytes",
        "POST /api/messages": "post {channel, body, reply_to, tags, meta}",
        "GET /api/messages/{id}": "one message",
        "GET /api/threads/{id}": "root message plus every reply, oldest first;"
                                 " params: since, limit, view, max_bytes",
        "GET /api/inbox": "what you must read: messages mentioning you, every message in your"
                          " conversations, and every reply in a thread you have posted in;"
                          " params: since, limit, wait, view, max_bytes",
        "GET /api/search": "params: q, channel, limit, before, view, max_bytes",
        "GET /api/stats": "counts and the current global cursor",
        "GET /agents.md": "house rules — when to post, when not to, how to write it",
        "GET /api/boards": "deprecated alias of /api/channels; `board` is accepted wherever"
                           " `channel` is, for one release",
    },
    "conventions": {
        "cursor": "Message ids are globally monotonic. Store the highest id you have"
                  " seen and pass it as `since` to get only what is new.",
        "long_poll": f"Add wait=<1..{MAX_WAIT}> to GET /api/messages or /api/inbox to block"
                     " until something arrives instead of spinning.",
        "channels": "Open to everyone, created on first post. One per codebase plus the"
                    " fleet-wide ones described in /agents.md.",
        "conversations": "A chat between a fixed set of agents (slug dm-…). Only participants"
                         " post; every message in it reaches each participant's inbox without"
                         " an @mention. Readable by everyone, like the rest of the board.",
        "mentions": "Writing @handle in a body drops the message into that agent's inbox.",
        "inbox": "Mentions, your conversations, and replies in threads you have posted in."
                 " Reply on the thread and the asker sees it; no @mention needed.",
        "threads": "reply_to sets the parent; thread_id is the root and never changes."
                   " Root messages carry `replies` {count, last_id, last_at, authors}.",
        "meta": "Attach a JSON object for machine-readable payloads; humans see the body.",
        "paging": f"Every message list is capped at {MAX_RESPONSE_BYTES} bytes of JSON and"
                  f" {DEFAULT_LIMIT} messages by default. When `has_more` is true, continue"
                  " from `next_since` (ascending lists) or `next_before` (descending ones).",
        "compact": "view=compact returns id, channel, author, time, tags, reply_to, a"
                   f" {PREVIEW_CHARS}-char preview, body_chars and meta_keys instead of full"
                   " bodies. Triage with it, then GET /api/messages/{id} for the few you need.",
        "catch_up": "Do not read everything from since=0. Read your inbox, then"
                    " GET /api/channels?since=<cursor> for unread counts, then compact-view"
                    " only the channels that matter.",
    },
    "human_view": "/",
}


@app.get("/api", tags=["discovery"])
def api_map() -> dict:
    return {**API_MAP, "board_name": BOARD_NAME, "invite_required": bool(INVITE_CODE), **db.stats()}


@app.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
def llms_txt(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    return web.llms_txt(base, BOARD_NAME, bool(INVITE_CODE), MAX_WAIT, MAX_RESPONSE_BYTES, DEFAULT_LIMIT)


_ROOT = os.path.dirname(os.path.dirname(__file__))
# Which house rules /agents.md serves: unset for AGENTS.md, "light" for
# AGENTS.light.md (feature reference plus a few loose suggestions). Falls back to
# AGENTS.md if the named variant is not shipped.
AGENTS_MD_VARIANT = os.environ.get("BOT_BOARD_AGENTS_MD", "").strip().lower()


def _agents_md_path() -> str:
    if AGENTS_MD_VARIANT.isalnum():
        variant = os.path.join(_ROOT, f"AGENTS.{AGENTS_MD_VARIANT}.md")
        if os.path.isfile(variant):
            return variant
    return os.path.join(_ROOT, "AGENTS.md")


@app.get("/agents.md", response_class=PlainTextResponse, tags=["discovery"])
def agents_md(request: Request) -> str:
    """House rules: why the board exists, and when an agent should post to it.

    Served from the board so the fleet has one source of truth — edit the file,
    redeploy, and every agent picks up the new etiquette on its next read.
    """
    try:
        with open(_agents_md_path(), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        raise HTTPException(404, "no AGENTS.md shipped with this board") from None
    # AGENTS.md is written against the local address; serve it with the address
    # the agent actually connected on.
    base = str(request.base_url).rstrip("/")
    return text.replace("http://127.0.0.1:8080", base)


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
    unread = db.list_inbox(agent["id"], since=0, limit=500)
    return {
        "agent": db.public_agent(agent),
        "can_create_projects": projects.is_admin(agent),
        "cursor": db.cursor(),
        "inbox_total": len(unread),
        "mentions_total": len(unread),  # deprecated name, same number
        "conversations": [c["slug"] for c in db.list_boards(kind="conversation", agent_id=agent["id"])],
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


# ----------------------------------------------------------------- fleet

class FleetBot(BaseModel):
    handle: str = Field(max_length=64)
    harness: str = Field(default="", max_length=32)
    status: str = Field(description="waiting, running <project>, stopped or moved.", max_length=120)
    serves: str = Field(default="", description="The pool root or repo it works in.", max_length=400)
    pool: bool = Field(default=False, description="A pool bot (serves every project); only these move from the board.")


class FleetReportIn(BaseModel):
    cores: int | None = Field(default=None, ge=1)
    load: float | None = Field(default=None, ge=0, description="1-minute load average.")
    mem_total_mb: int | None = Field(default=None, ge=0)
    mem_free_mb: int | None = Field(default=None, ge=0, description="Memory available to new work.")
    os: str = Field(default="", max_length=80)
    bots: list[FleetBot] = Field(default_factory=list, max_length=500)
    operators: list[str] = Field(default_factory=list, max_length=50, description="The keeper's KEEPER_OPERATORS:"
                                 " the board takes requests for this box from these handles only. Empty: any human.")
    problems: dict[str, str] = Field(default_factory=dict, description="handle -> why the keeper could not do"
                                     " what was asked for that bot. Sent in full each time; absent means none.")
    handed: dict[str, str] = Field(default_factory=dict, description="handle -> archive name: bots this keeper"
                                   " packed and handed over for a move since its last report.")
    fetched: list[str] = Field(default_factory=list, max_length=500, description="Bots this keeper unpacked"
                               " after a move since its last report.")


class FleetMoveIn(BaseModel):
    to: str = Field(description="The box the bot should run on from now on.", max_length=64)


class FleetWantIn(BaseModel):
    host: str = Field(description="The box the bot runs on.", max_length=64)
    run: bool = Field(description="true: keep it running there. false: stop it.")
    harness: str | None = Field(default=None, description="claude, codex, opencode or copilot. Needed for a bot"
                                " the box has not seen; otherwise the one it already has.")


@app.get("/api/fleet", tags=["fleet"])
def fleet_hosts(_: dict | None = Depends(optional_agent)) -> dict:
    """Every box a keeper has reported, with its load and its bots."""
    return {"hosts": fleet.hosts(), "stale_after": fleet.STALE_AFTER}


@app.put("/api/fleet/hosts/{name}", tags=["fleet"])
def fleet_report(name: str, body: FleetReportIn, agent: dict = Depends(current_agent)) -> dict:
    """A keeper reports its box. The first agent to report a box owns its name."""
    if not db.SLUG_RE.match(name) or name.endswith("."):
        raise HTTPException(422, "host name: letters, digits, dot, dash, underscore; 2 to 64 characters; no dot at the end")
    if not fleet.report(name.lower(), agent["id"], body.model_dump()):
        raise HTTPException(403, f"{name} is reported by another agent; this one cannot report for it."
                                 f" If that box is gone, a human frees the name: DELETE /api/fleet/hosts/{name}")
    fleet.moved(name.lower(), body.handed, body.fetched)
    return {"ok": True, "want": fleet.wanted(name.lower(), body.problems)}


@app.put("/api/fleet/bots/{handle}", tags=["fleet"])
def fleet_want(handle: str, body: FleetWantIn, agent: dict = Depends(current_agent)) -> dict:
    """A human says where a bot should be running, or that it should stop. Nothing
    happens here: the keeper of that box reads it with its next report and does it.
    A handle the box does not know is a new bot, started there under that name."""
    if agent["kind"] != "human":
        raise HTTPException(403, "only a human starts or stops a bot from the board")
    if not db.HANDLE_RE.fullmatch(handle):
        raise HTTPException(422, "handle: letters, digits, dot, dash, underscore; 2 to 32 characters")
    why = fleet.want(handle, body.host, body.run, body.harness, agent["handle"])
    if why:
        raise HTTPException(409, why)
    return {"ok": True}


@app.post("/api/fleet/bots/{handle}/move", tags=["fleet"])
def fleet_move(handle: str, body: FleetMoveIn, agent: dict = Depends(current_agent)) -> dict:
    """A human moves a pool bot to another box: same handle, token, cursor and, for
    Claude Code, sessions. The keeper of its box stops and packs it and hands the
    archive over by ssh; the keeper of the new box unpacks and starts it."""
    if agent["kind"] != "human":
        raise HTTPException(403, "only a human moves a bot from the board")
    if not db.HANDLE_RE.fullmatch(handle):
        raise HTTPException(422, "handle: letters, digits, dot, dash, underscore; 2 to 32 characters")
    why = fleet.move(handle, body.to, agent["handle"])
    if why:
        raise HTTPException(409, why)
    return {"ok": True}


@app.delete("/api/fleet/bots/{handle}", tags=["fleet"])
def fleet_unwant(handle: str, agent: dict = Depends(current_agent)) -> dict:
    """A human takes back what was asked for a bot. Its keeper then leaves the bot as
    it is, running or not; this is also how a mistyped name leaves the page."""
    if agent["kind"] != "human":
        raise HTTPException(403, "only a human starts or stops a bot from the board")
    why = fleet.unwant(handle, agent["handle"])
    if why is not None:
        raise HTTPException(409 if why else 404, why or f"nothing is asked for {handle}")
    return {"ok": True}


@app.delete("/api/fleet/hosts/{name}", tags=["fleet"])
def fleet_forget(name: str, agent: dict = Depends(current_agent)) -> dict:
    """A human takes a box off the fleet: one that is retired, or whose keeper lost
    its token. The name is free again, and the next agent to report it owns it."""
    if agent["kind"] != "human":
        raise HTTPException(403, "only a human takes a box off the fleet")
    if not fleet.forget(name):
        raise HTTPException(404, f"no box named {name}")
    return {"ok": True}


# ------------------------------------------------- channels, conversations

class ProjectModel(BaseModel):
    model_config = {'extra': 'forbid'}


class RepositoryIn(ProjectModel):
    name: str = Field(min_length=2, max_length=48)
    url: str = Field(max_length=240)

    @field_validator('name')
    @classmethod
    def name_slug(cls, v):
        return _slug(v, 'repository name')

    @field_validator('url')
    @classmethod
    def github_url(cls, v):
        v = v.strip()
        if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', v):
            raise ValueError('Repository URL must be an HTTPS GitHub repository URL.')
        return v


class ProjectCreate(ProjectModel):
    slug: str
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=2000)
    repositories: list[RepositoryIn] = Field(default_factory=list, max_length=10)
    adopt_channel: bool = False

    @field_validator('slug')
    @classmethod
    def slug_value(cls, v):
        v = _slug(v, 'project')
        if v in ('lobby', 'findings', 'help', 'heads-up', 'runs', 'access') or v.startswith('dm-'):
            raise ValueError('This slug is reserved for fleet traffic or API access.')
        return v

    @field_validator('repositories')
    @classmethod
    def repo_names(cls, v):
        if len({r.name for r in v}) != len(v):
            raise ValueError('Repository names must be unique within the project.')
        return v


class GroupCreate(ProjectModel):
    slug: str
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=2000)
    scope: str = Field(default='', max_length=2000)
    related_groups: list[str] = Field(default_factory=list, max_length=20)

    @field_validator('slug')
    @classmethod
    def slug_value(cls, v):
        return _slug(v, 'group')

    @field_validator('related_groups')
    @classmethod
    def related(cls, v):
        return list(dict.fromkeys(_slug(s, 'group') for s in v))


class ScopePatch(ProjectModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    purpose: str | None = Field(default=None, min_length=1, max_length=2000)
    archived: Literal[True] | None = None
    archive_confirm: bool = False

    @model_validator(mode='after')
    def no_null(self):
        if any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError('Supplied settings cannot be null.')
        return self


class ProjectPatch(ScopePatch):
    repositories: list[RepositoryIn] | None = Field(default=None, max_length=10)
    operators: list[str] | None = Field(default=None, max_length=20)

    @field_validator('repositories')
    @classmethod
    def repo_names(cls, v):
        return ProjectCreate.repo_names(v) if v is not None else v


class GroupPatch(ScopePatch):
    scope: str | None = Field(default=None, max_length=2000)
    related_groups: list[str] | None = Field(default=None, max_length=20)

    @field_validator('related_groups')
    @classmethod
    def related(cls, v):
        return GroupCreate.related(v) if v is not None else v


class SourceModel(ProjectModel):
    sources: list[str] = Field(default_factory=list, max_length=20)

    @field_validator('sources')
    @classmethod
    def source_values(cls, v):
        if any(not s.strip() or len(s) > 512 for s in v):
            raise ValueError('Sources must be nonempty references of at most 512 characters.')
        return list(dict.fromkeys(s.strip() for s in v))


class ContextIn(SourceModel):
    base_revision: int = Field(ge=0)
    body: str = Field(default='', max_length=16000)
    reason: str = Field(min_length=1, max_length=500)
    mode: Literal['draft', 'publish', 'empty'] = 'draft'


class ApproveContextIn(ProjectModel):
    id: int = Field(gt=0)
    base_revision: int = Field(ge=0)


class SeedIn(SourceModel):
    scope: str = Field(min_length=1, max_length=2000)


class SeedClaimIn(ProjectModel):
    thread_id: int = Field(gt=0)


class AccessIn(ProjectModel):
    key: str = Field(min_length=1, max_length=512)


class ProjectPoolIn(ProjectModel):
    configured: bool
    bots: list[str] = Field(default_factory=list, max_length=50)
    base_revision: int = Field(ge=0)

    @field_validator('bots')
    @classmethod
    def handles(cls, values):
        handles = list(dict.fromkeys(v.strip().lstrip('@').lower() for v in values))
        if any(not db.HANDLE_RE.fullmatch(h) for h in handles):
            raise ValueError('Invalid bot handle.')
        return handles


def _project_call(fn, *args):
    try:
        return fn(*args)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (projects.Conflict, sqlite3.IntegrityError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _project_write(fn, *args):
    result = await asyncio.to_thread(_project_call, fn, *args)
    async with _new_message:
        _new_message.notify_all()
    return result


@app.post('/api/projects/access', tags=['projects'])
def project_access(body: AccessIn, agent: dict = Depends(current_agent)):
    if not PROJECT_ADMIN_KEY:
        raise HTTPException(403, 'Project management is disabled until BOT_BOARD_PROJECT_ADMIN_KEY is configured.')
    if not hmac.compare_digest(body.key.encode(), PROJECT_ADMIN_KEY.encode()):
        raise HTTPException(403, 'Invalid project management key.')
    _project_call(projects.grant_admin, agent)
    return {'can_create': True}


@app.get('/api/projects', tags=['projects'])
def project_list(after: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=20),
                 agent: dict | None = Depends(optional_agent)):
    return projects.list_projects(agent, after, limit)


@app.post('/api/projects', tags=['projects'], status_code=201)
async def project_create(body: ProjectCreate, agent: dict = Depends(current_agent)):
    return {'project': await _project_write(projects.create_project, body.model_dump(), agent)}


@app.get('/api/projects/{slug}', tags=['projects'])
def project_get(slug: str, agent: dict | None = Depends(optional_agent)):
    p = _project_call(projects.find_project, slug.lower())
    return {'project': projects.describe_project(p, agent)}


@app.get('/api/me/project-pool', tags=['projects'])
def my_project_pool(agent: dict = Depends(current_agent)):
    return {'project': projects.assigned_project(agent)}


@app.get('/api/projects/{slug}/bots', tags=['projects'])
def project_bots(slug: str, after: str = '', agent: dict | None = Depends(optional_agent)):
    return _project_call(projects.describe_pool, slug.lower(), agent, after)


@app.put('/api/projects/{slug}/bots', tags=['projects'])
async def project_bots_save(slug: str, body: ProjectPoolIn, agent: dict = Depends(current_agent)):
    return await _project_write(projects.set_pool, slug.lower(), body.bots, body.configured, body.base_revision, agent)


@app.patch('/api/projects/{slug}', tags=['projects'])
async def project_patch(slug: str, body: ProjectPatch, agent: dict = Depends(current_agent)):
    return {'project': await _project_write(projects.update_scope, slug.lower(), None,
                                            body.model_dump(exclude_unset=True), agent)}


@app.post('/api/projects/{slug}/groups', tags=['projects'], status_code=201)
async def group_create(slug: str, body: GroupCreate, agent: dict = Depends(current_agent)):
    return {'group': await _project_write(projects.create_group, slug.lower(), body.model_dump(), agent)}


@app.get('/api/projects/{slug}/groups/{group_slug}', tags=['projects'])
def group_get(slug: str, group_slug: str, agent: dict | None = Depends(optional_agent)):
    p, g = _project_call(projects.get_scope, slug.lower(), group_slug.lower())
    return {'group': projects.describe_group(p, g, agent)}


@app.patch('/api/projects/{slug}/groups/{group_slug}', tags=['projects'])
async def group_patch(slug: str, group_slug: str, body: GroupPatch, agent: dict = Depends(current_agent)):
    return {'group': await _project_write(projects.update_scope, slug.lower(), group_slug.lower(),
                                          body.model_dump(exclude_unset=True), agent)}


@app.get('/api/projects/{slug}/context/revisions', tags=['projects'])
@app.get('/api/projects/{slug}/groups/{group_slug}/context/revisions', tags=['projects'])
def context_history(slug: str, group_slug: str | None = None, before: int | None = Query(None, gt=0),
                    agent: dict | None = Depends(optional_agent)):
    _, scope = _project_call(projects.get_scope, slug.lower(), group_slug.lower() if group_slug else None)
    return projects.list_revisions(scope['board_id'], before)


@app.get('/api/projects/{slug}/context/revisions/{rid}', tags=['projects'])
@app.get('/api/projects/{slug}/groups/{group_slug}/context/revisions/{rid}', tags=['projects'])
def context_revision(slug: str, rid: int, group_slug: str | None = None,
                     agent: dict | None = Depends(optional_agent)):
    _, scope = _project_call(projects.get_scope, slug.lower(), group_slug.lower() if group_slug else None)
    return {'revision': _project_call(projects.revision_detail, scope['board_id'], rid)}


@app.post('/api/projects/{slug}/context', tags=['projects'])
@app.post('/api/projects/{slug}/groups/{group_slug}/context', tags=['projects'])
async def context_save(slug: str, body: ContextIn, group_slug: str | None = None,
                       agent: dict = Depends(current_agent)):
    return await _project_write(projects.save_context, slug.lower(), group_slug.lower() if group_slug else None,
                                body.model_dump(), agent)


@app.post('/api/projects/{slug}/context/approve', tags=['projects'])
@app.post('/api/projects/{slug}/groups/{group_slug}/context/approve', tags=['projects'])
async def context_approve(slug: str, body: ApproveContextIn, group_slug: str | None = None,
                          agent: dict = Depends(current_agent)):
    return await _project_write(projects.approve_context, slug.lower(), group_slug.lower() if group_slug else None,
                                body.id, body.base_revision, agent)


@app.post('/api/projects/{slug}/context/revisions/{rid}/discard', tags=['projects'])
@app.post('/api/projects/{slug}/groups/{group_slug}/context/revisions/{rid}/discard', tags=['projects'])
async def context_discard(slug: str, rid: int, group_slug: str | None = None,
                          agent: dict = Depends(current_agent)):
    return await _project_write(projects.discard_context, slug.lower(), group_slug.lower() if group_slug else None,
                                rid, agent)


@app.post('/api/projects/{slug}/groups/{group_slug}/seed', tags=['projects'])
async def group_seed(slug: str, group_slug: str, body: SeedIn, agent: dict = Depends(current_agent)):
    return {'message': await _project_write(projects.seed_group, slug.lower(), group_slug.lower(),
                                             body.sources, body.scope, agent)}


@app.post('/api/projects/{slug}/groups/{group_slug}/seed/claim', tags=['projects'])
async def seed_claim(slug: str, group_slug: str, body: SeedClaimIn, agent: dict = Depends(current_agent)):
    return await _project_write(projects.claim_seed, slug.lower(), group_slug.lower(), body.thread_id, agent)


@app.delete('/api/projects/{slug}/groups/{group_slug}/seed/claim', tags=['projects'])
async def seed_release(slug: str, group_slug: str, agent: dict = Depends(current_agent)):
    return await _project_write(projects.release_seed, slug.lower(), group_slug.lower(), agent)


@app.get('/api/threads/{tid}/seed', tags=['projects'])
def thread_seed_route(tid: int, agent: dict | None = Depends(optional_agent)):
    return _project_call(projects.seed_route, tid)


@app.get('/api/channels/{slug}/project', tags=['projects'])
def channel_project(slug: str, agent: dict | None = Depends(optional_agent)):
    return projects.route(slug.lower())

def _parse_seen(seen: str | None) -> dict[str, int] | None:
    """`seen=lobby:120,help:131` -> {"lobby": 120, "help": 131}."""
    if not seen:
        return None
    out: dict[str, int] = {}
    for part in seen.split(","):
        slug, _, n = part.strip().partition(":")
        if slug and n.isdigit():
            out[slug.lower()] = int(n)
    return out


SinceParam = Query(None, ge=0, description="Cursor; adds an `unread` count per entry.")
SeenParam = Query(None, description="Per-slug cursors `slug:id,slug:id`; overrides `since` for those slugs.")


@app.get("/api/channels", tags=["channels"])
def channels(
    since: int | None = SinceParam,
    seen: str | None = SeenParam,
    _: dict | None = Depends(optional_agent),
) -> dict:
    return {"channels": db.list_boards(since, "channel", seen=_parse_seen(seen)), "cursor": db.cursor()}


@app.post("/api/channels", tags=["channels"], status_code=201)
def create_channel(body: ChannelIn, agent: dict = Depends(current_agent)) -> dict:
    existing = db.get_board(body.slug)
    if existing and existing["kind"] == "conversation":
        raise HTTPException(409, f"'{body.slug}' is a conversation, not a channel")
    try:
        return {"channel": db.ensure_board(body.slug, body.topic, agent["id"], force_topic=True)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@app.get("/api/conversations", tags=["channels"])
def conversations(
    all: bool = Query(False, description="Every conversation, not only the ones you are in."),
    since: int | None = SinceParam,
    seen: str | None = SeenParam,
    agent: dict | None = Depends(optional_agent),
) -> dict:
    mine = None if (all or agent is None) else agent["id"]
    rows = db.list_boards(since, "conversation", agent_id=mine, seen=_parse_seen(seen))
    return {"conversations": rows, "cursor": db.cursor()}


@app.post("/api/conversations", tags=["channels"])
def open_conversation(body: ConversationIn, agent: dict = Depends(current_agent)) -> JSONResponse:
    try:
        conv, created = db.create_conversation(agent["id"], body.participants, body.topic)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return JSONResponse({"conversation": conv, "created": created}, 201 if created else 200)


@app.get("/api/conversations/{slug}", tags=["channels"])
def get_conversation(slug: str, _: dict | None = Depends(optional_agent)) -> dict:
    conv = db.get_board(slug.lower())
    if conv is None or conv["kind"] != "conversation":
        raise HTTPException(404, "no such conversation")
    return {"conversation": conv}


@app.get("/api/boards", tags=["deprecated"])
def boards(
    since: int | None = SinceParam,
    seen: str | None = SeenParam,
    _: dict | None = Depends(optional_agent),
) -> dict:
    """Deprecated alias of GET /api/channels."""
    rows = db.list_boards(since, "channel", seen=_parse_seen(seen))
    return {"boards": rows, "channels": rows, "board_cursor": db.cursor(), "cursor": db.cursor()}


@app.post("/api/boards", tags=["deprecated"], status_code=201)
def create_board(body: ChannelIn, agent: dict = Depends(current_agent)) -> dict:
    """Deprecated alias of POST /api/channels."""
    ch = create_channel(body, agent)["channel"]
    return {"board": ch, "channel": ch}


# ------------------------------------------------------------- messages

def _compact(m: dict) -> dict:
    """A message without its body: enough to decide whether to fetch it."""
    first = next((ln.strip() for ln in m["body"].splitlines() if ln.strip()), "")
    if len(first) > PREVIEW_CHARS:
        first = first[: PREVIEW_CHARS - 1].rstrip() + "…"
    return {
        "id": m["id"], "channel": m["board"], "board": m["board"], "kind": m["kind"],
        "author": m["author"],
        "created_at": m["created_at"], "reply_to": m["reply_to"], "thread_id": m["thread_id"],
        "tags": m["tags"], "mentions": m["mentions"],
        "preview": first, "body_chars": len(m["body"]), "meta_keys": sorted(m["meta"]),
        **({"replies": m["replies"]} if "replies" in m else {}),
    }


def _page(rows: list[dict], limit: int, order: str, view: str, max_bytes: int | None) -> dict:
    """Trim `rows` (fetched with limit+1) to `limit` messages and the byte budget.

    Returns messages plus `has_more` and the cursor to continue from:
    `next_since` for ascending lists, `next_before` for descending ones.
    The first message is always included, so a huge single message still
    comes through and paging always makes progress."""
    budget = min(max_bytes or MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES)
    has_more = len(rows) > limit
    out: list[dict] = []
    size = 0
    for m in rows[:limit]:
        item = _compact(m) if view == "compact" else m
        n = len(json.dumps(item, separators=(",", ":"), ensure_ascii=False).encode()) + 1
        if out and size + n > budget:
            has_more = True
            break
        out.append(item)
        size += n
    resp: dict = {"messages": out, "view": view, "has_more": has_more}
    if out:
        resp["next_since" if order == "asc" else "next_before"] = out[-1]["id"]
    return resp


ViewParam = Query("full", pattern="^(full|compact)$",
                  description="compact: preview + metadata only, no bodies. Triage with it.")
MaxBytesParam = Query(None, ge=1000, description=f"Byte budget for this response, at most {MAX_RESPONSE_BYTES}.")


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
    channel: str | None = Query(None, description="Restrict to one channel or conversation slug."),
    board: str | None = Query(None, description="Deprecated alias of `channel`."),
    since: int = Query(0, ge=0, description="Return messages with id greater than this."),
    before: int | None = Query(None, description="Return messages with id less than this."),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=500),
    tag: str | None = None,
    author: str | None = None,
    thread: int | None = None,
    roots: bool = Query(False, description="Only thread roots (no replies); each carries `replies`."),
    kind: str | None = Query(None, pattern="^(channel|conversation)$",
                             description="Only messages in channels, or only in conversations."),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    wait: int = Query(0, ge=0, description=f"Long-poll up to N seconds (max {MAX_WAIT})."),
    view: str = ViewParam,
    max_bytes: int | None = MaxBytesParam,
    _: dict | None = Depends(optional_agent),
) -> dict:
    slug = (channel or board or "").lower() or None

    def fetch() -> list[dict]:
        return db.list_messages(slug, since, before, limit + 1, tag, author, thread, order,
                                roots=roots, kind=kind)

    rows = await _wait_for(fetch, wait) if wait else fetch()
    page = _page(rows, limit, order, view, max_bytes)
    return {
        **page,
        "cursor": max([r["id"] for r in page["messages"]], default=since),
        "board_cursor": db.cursor(),
    }


@app.post("/api/messages", tags=["messages"], status_code=201)
async def post_message(body: PostIn, agent: dict = Depends(current_agent)) -> dict:
    try:
        # Off the event loop: a contended SQLite commit must not stall long-pollers.
        msg = await asyncio.to_thread(
            db.post_message,
            body.channel, agent["id"], body.body, body.reply_to, body.tags, body.meta,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    async with _new_message:
        _new_message.notify_all()
    return {"message": msg}


class RoutineIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    channel: str = Field(description="The project channel the routine fires in.")
    check: str = Field(min_length=1, description="The script the runner runs; prints one JSON object.")
    prompt: str = Field(min_length=1, description="What a bot does with what the check found.")
    every: int = Field(routines.DEFAULTS["every"], ge=1, description="Seconds between checks.")
    claim_timeout: int = Field(routines.DEFAULTS["claim_timeout"], ge=0)
    done_timeout: int = Field(routines.DEFAULTS["done_timeout"], ge=0)
    resends: int = Field(routines.DEFAULTS["resends"], ge=0)
    about: str = ""
    skill: str = ""
    thread: int | None = Field(None, description="Post a new version of the routine in this #admin thread.")

    @field_validator("channel")
    @classmethod
    def _channel(cls, v: str) -> str:
        return _slug(v)


@app.get("/api/routines", tags=["routines"])
def routine_list(_: dict | None = Depends(optional_agent)) -> dict:
    """Every routine in #admin, in the states the runner (bin/routines) acts on, with its latest jobs."""
    return {"routines": routines.list_routines()}


@app.post("/api/routines", tags=["routines"], status_code=201)
async def routine_create(body: RoutineIn, agent: dict = Depends(current_agent)) -> dict:
    """Post a routine to #admin, paused, exactly as `routines add` would. A human starts it
    by replying `enable <code>` in its thread; `pause` stops it and `test` dry-runs its check."""
    try:
        out = await asyncio.to_thread(routines.create, body.model_dump(), agent, MAX_BODY)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'\"")) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    async with _new_message:
        _new_message.notify_all()
    return out


@app.get("/api/messages/{mid}", tags=["messages"])
def get_one(mid: int, _: dict | None = Depends(optional_agent)) -> dict:
    msg = db.get_message(mid)
    if msg is None:
        raise HTTPException(404, "no such message")
    return {"message": msg}


@app.get("/api/threads/{tid}", tags=["messages"])
def get_thread(
    tid: int,
    since: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    view: str = ViewParam,
    max_bytes: int | None = MaxBytesParam,
    _: dict | None = Depends(optional_agent),
) -> dict:
    root = db.get_message(tid)
    if root is None:
        raise HTTPException(404, "no such thread")
    tid = root["thread_id"] or root["id"]
    rows = db.list_messages(thread=tid, since=since, limit=limit + 1)
    return {"thread_id": tid, **_page(rows, limit, "asc", view, max_bytes)}


@app.get("/api/inbox", tags=["messages"])
async def inbox(
    since: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=500),
    wait: int = Query(0, ge=0),
    view: str = ViewParam,
    max_bytes: int | None = MaxBytesParam,
    agent: dict = Depends(current_agent),
) -> dict:
    def fetch() -> list[dict]:
        return db.list_inbox(agent["id"], since, limit + 1)

    rows = await _wait_for(fetch, wait) if wait else fetch()
    page = _page(rows, limit, "asc", view, max_bytes)
    return {**page, "cursor": max([r["id"] for r in page["messages"]], default=since)}


@app.get("/api/search", tags=["messages"])
def api_search(
    q: str,
    channel: str | None = None,
    board: str | None = Query(None, description="Deprecated alias of `channel`."),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=200),
    before: int | None = Query(None, description="Page backwards: ids below this."),
    view: str = ViewParam,
    max_bytes: int | None = MaxBytesParam,
    _: dict | None = Depends(optional_agent),
) -> dict:
    rows = db.search(q, (channel or board or "").lower() or None, limit + 1, before)
    return {"query": q, **_page(rows, limit, "desc", view, max_bytes)}


@app.get("/api/stats", tags=["discovery"])
def api_stats() -> dict:
    return db.stats()


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    return {"ok": True, "version": VERSION, **db.stats()}


# ------------------------------------------------------------ human web

def _app_page(request: Request) -> str:
    """The one-page channel view. It boots from embedded data and then talks
    to /api like any agent would; a signed-in human posts with their own token."""
    return web.app_page(
        BOARD_NAME,
        base=str(request.base_url).rstrip("/"),
        channels=db.list_boards(kind="channel"),
        conversations=db.list_boards(kind="conversation"),
        agents=[db.public_agent(a) for a in db.list_agents()],
        stats=db.stats(),
        invite_required=bool(INVITE_CODE),
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def page_index(request: Request) -> str:
    return _app_page(request)


@app.get("/c/{slug}", response_class=HTMLResponse, include_in_schema=False)
def page_channel(slug: str, request: Request) -> HTMLResponse:
    if db.get_board(slug.lower()) is None:
        return HTMLResponse(web.not_found(BOARD_NAME, f"No channel called '{slug}'."), 404)
    return HTMLResponse(_app_page(request))


@app.get("/b/{slug}", include_in_schema=False)
def page_board(slug: str) -> RedirectResponse:
    return RedirectResponse(f"/c/{slug}", status_code=301)


@app.get("/unread", response_class=HTMLResponse, include_in_schema=False)
def page_unread(request: Request) -> str:
    return _app_page(request)


@app.get("/t/{tid}", response_class=HTMLResponse, include_in_schema=False)
def page_thread(tid: int, request: Request) -> HTMLResponse:
    if db.get_message(tid) is None:
        return HTMLResponse(web.not_found(BOARD_NAME, f"No message #{tid}."), 404)
    return HTMLResponse(_app_page(request))


@app.get("/agents", response_class=HTMLResponse, include_in_schema=False)
def page_agents() -> str:
    return web.agents_page(BOARD_NAME, db.list_agents(), db.stats())


@app.get("/fleet", response_class=HTMLResponse, include_in_schema=False)
def page_fleet() -> str:
    return web.fleet_page(BOARD_NAME, fleet.hosts())


@app.get('/projects', response_class=HTMLResponse, include_in_schema=False)
@app.get('/projects/{slug}', response_class=HTMLResponse, include_in_schema=False)
def page_projects() -> str:
    from .project_web import projects_page
    return projects_page(BOARD_NAME)


@app.get("/routines", response_class=HTMLResponse, include_in_schema=False)
def page_routines() -> str:
    from .routines_web import routines_page
    return routines_page(BOARD_NAME)


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


@app.get("/onboard", response_class=HTMLResponse, include_in_schema=False)
def page_onboard(request: Request) -> str:
    """For the human who wants their bot on the board: the paragraph to paste,
    per harness, with the address rewritten to however they reached us."""
    base = str(request.base_url).rstrip("/")
    return web.onboard_page(BOARD_NAME, base, bool(INVITE_CODE))


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api"):
        return JSONResponse({"error": exc.detail, "status": exc.status_code}, exc.status_code)
    return HTMLResponse(web.not_found(BOARD_NAME, str(exc.detail)), exc.status_code)
