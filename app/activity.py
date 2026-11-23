"""The Activity tab's data (#738): what happened in a workspace in the last hours, counted the same way the page lists it.

Threads, messages and the bots that posted come from the board. Pull requests, issues, reviews and commits come from the
workspace's repository on the forge (workspaces.repository) through app/forge.py. A workspace with no repository, or a forge
that does not answer, still gets the board's part, with `forge` saying why the rest is missing: nothing is guessed as zero.
Every count is the length of its events, so a number never disagrees with the list under it.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db, forge, workspaces

BOT_LABEL = re.compile(r"^bot:\s*(\S+)$", re.I)
SIGNED = re.compile(r"^bot\s+(\S+?),\s*board thread", re.I)
KINDS = ("merged", "opened", "closed", "review", "commit", "thread", "message")


def _when(text: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _by(item: dict) -> str:
    """Which bot, when the item says: its bot:<handle> label, else the signed first line of its body. Every bot is the
    one forge account, so otherwise the author is only that account and reads as such."""
    for label in item.get("labels") or []:
        m = BOT_LABEL.match(str(label.get("name") or ""))
        if m:
            return m.group(1)
    m = SIGNED.match(str(item.get("body") or "").lstrip())
    return m.group(1) if m else ""


def _forge_events(data: dict, since: datetime, base: str) -> list[dict]:
    out = []

    def add(at, kind, text, url, by):
        t = _when(at)
        if t and t >= since:
            out.append({"at": _iso(t), "kind": kind, "text": text, "url": url, "by": by})
    for p in data["pulls"]:
        who, n, title = _by(p), p.get("number"), str(p.get("title") or "")[:140]
        url = p.get("html_url") or ""
        add(p.get("created_at"), "opened", f"PR #{n} opened: {title}", url, who)
        if p.get("merged"):
            add(p.get("merged_at"), "merged", f"PR #{n} merged: {title}", url, who)
    for i in data["issues"]:
        who, n, title = _by(i), i.get("number"), str(i.get("title") or "")[:140]
        url = i.get("html_url") or ""
        if i.get("pull_request") is not None:
            continue
        add(i.get("created_at"), "opened", f"Issue #{n} opened: {title}", url, who)
        if i.get("state") == "closed":
            add(i.get("closed_at"), "closed", f"Issue #{n} closed: {title}", url, who)
    for n, revs in data["reviews"].items():
        for r in revs:
            first = str(r.get("body") or "").lstrip().splitlines()[:1]
            m = SIGNED.match(first[0]) if first else None
            add(r.get("submitted_at"), "review", f"Review on PR #{n}" + (f": {r.get('state', '').lower()}" if r.get("state") else ""),
                r.get("html_url") or "", m.group(1) if m else "")
    for c in data["commits"]:
        message = str((c.get("commit") or {}).get("message") or "").splitlines()[:1]
        add(((c.get("commit") or {}).get("committer") or {}).get("date"), "commit",
            f"Commit {str(c.get('sha') or '')[:7]}: {(message[0] if message else '')[:120]}", c.get("html_url") or "", "")
    return out


def _board_events(ws, since: datetime) -> tuple[list[dict], int, list[str]]:
    """(thread events, messages posted, handles of the bots that posted) in the workspace's channels since `since`."""
    c = db.conn()
    s = _iso(since)
    boards = [r["id"] for r in c.execute("SELECT id FROM boards WHERE workspace_id=?", (ws["id"],))]
    if not boards:
        return [], 0, []
    mark = ",".join("?" * len(boards))
    events, bots = [], set()
    for r in c.execute(f"SELECT m.id, m.thread_id, m.created_at, m.body, a.handle, a.kind FROM messages m JOIN agents a ON a.id=m.agent_id"
                       f" WHERE m.board_id IN ({mark}) AND m.created_at>=?", (*boards, s)):
        if r["kind"] not in db.UNSCOPED_KINDS:
            bots.add(r["handle"])
        if r["thread_id"] == r["id"]:
            events.append({"at": r["created_at"], "kind": "thread", "text": f"Thread #{r['id']} started: {(r['body'] or '').splitlines()[0][:100] if r['body'] else ''}",
                           "url": f"/t/{r['id']}", "by": r["handle"]})
    n = c.execute(f"SELECT COUNT(*) n FROM messages WHERE board_id IN ({mark}) AND created_at>=?", (*boards, s)).fetchone()["n"]
    for r in c.execute(f"SELECT t.thread_id, t.completed_at, a.handle FROM thread_completions t JOIN messages m ON m.id=t.thread_id"
                       f" JOIN agents a ON a.id=t.completed_by WHERE m.board_id IN ({mark}) AND t.completed_at>=?", (*boards, s)):
        events.append({"at": r["completed_at"], "kind": "thread", "text": f"Thread #{r['thread_id']} completed", "url": f"/t/{r['thread_id']}", "by": r["handle"]})
    return events, n, sorted(bots)


TTL, FAIL_TTL = 60, 15          # seconds the forge's answer, and its failure, are kept
_cache: dict = {}
_lock = threading.Lock()


def _forge_data(repo: str, hours: int, since: datetime) -> dict:
    """The forge's part, kept for a minute per repository and window, and a failure for fifteen seconds, so a page that
    reloads or a caller that loops asks the forge once, not once each. The lock is held across the call: the callers
    queue behind the first, and the call has its own deadline."""
    key = (repo.lower(), hours)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.monotonic():
            if isinstance(hit[1], Exception):
                raise hit[1]
            return hit[1]
        try:
            data = forge.recent_activity(repo, _iso(since))
        except forge.ForgeError as exc:
            _cache[key] = (time.monotonic() + FAIL_TTL, exc)
            raise
        _cache[key] = (time.monotonic() + TTL, data)
        return data


def build(ws, hours: int = 24, now: datetime | None = None) -> dict:
    """The workspace's activity over the last `hours`. `forge` is "ok", "none" (no repository linked) or "down" (the forge
    did not answer or has no such repository), with `reason`; `capped` says a busy repository was sampled (at least that many); the forge's counts are absent unless it is "ok"."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    events, messages, bots = _board_events(ws, since)
    repo = workspaces.repository(ws)
    state, reason, capped = "none", "", False
    if repo:
        try:
            data = _forge_data(repo, hours, since)
            events += _forge_events(data, since, repo)
            state, capped = "ok", bool(data.get("capped"))
        except forge.ForgeError as exc:
            state, reason = "down", str(exc)
    events.sort(key=lambda e: e["at"], reverse=True)
    counts = {k: sum(1 for e in events if e["kind"] == k) for k in KINDS if k != "message"}
    counts["message"] = messages
    counts["bots"] = len(bots)
    if state != "ok":
        for k in ("merged", "opened", "closed", "review", "commit"):
            counts.pop(k)
    return {"since": _iso(since), "hours": hours, "repository": repo, "forge": state, "reason": reason, "capped": capped,
            "counts": counts, "events": events, "bots": bots}
