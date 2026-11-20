"""Team agreements (#622): the templates the board ships under docs/team/, and the agreement it seeds in a workspace.

A workspace's `team/agreement.md` says how its bots work together. The base template names the sections every
agreement keeps and, under each, the question an admin bot asks about it. A team template is a complete agreement
to start from. `default` is today's fleet behaviour, and is what a new workspace gets. A file under
docs/team/workspaces/<slug>.md is the agreement of the workspace with that slug.
"""
from __future__ import annotations

import os
import re

from . import db, registry

ROOT = os.path.dirname(os.path.dirname(__file__))
TEAM_DIR = os.path.join(ROOT, "docs", "team")
BASE = os.path.join(TEAM_DIR, "agreement-template.md")
TEMPLATES_DIR = os.path.join(TEAM_DIR, "templates")
WORKSPACES_DIR = os.path.join(TEAM_DIR, "workspaces")
AGREEMENT = "team/agreement.md"
DEFAULT = "default"
SECTIONS = ("People", "Ownership", "Splitting work", "Consulting", "Review", "Merge", "Disagreement", "Roles")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def base() -> str:
    """The base template: the sections every agreement keeps, each with the `> Ask:` question for it."""
    return _read(BASE)


def templates() -> list[dict]:
    """The team templates by name, title and description, as an admin bot offers them."""
    out = []
    if os.path.isdir(TEMPLATES_DIR):
        for fn in sorted(os.listdir(TEMPLATES_DIR)):
            if fn.endswith(".md"):
                front, _ = registry.parse_front(_read(os.path.join(TEMPLATES_DIR, fn)))
                name = str(front.get("name") or fn[:-3]).strip().lower()
                out.append({"name": name, "title": str(front.get("title") or name),
                            "description": str(front.get("description") or "")})
    return out


def template(name: str) -> dict:
    """One team template: its name, title, description and the agreement text. KeyError when there is none."""
    name = (name or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
        raise KeyError(f"no team template named {name}")
    path = os.path.join(TEMPLATES_DIR, name + ".md")
    if not os.path.isfile(path):
        raise KeyError(f"no team template named {name}")
    front, body = registry.parse_front(_read(path))
    return {"name": name, "title": str(front.get("title") or name),
            "description": str(front.get("description") or ""), "body": body.strip() + "\n"}


def agreement_for(slug: str) -> str:
    """The agreement a workspace starts with: its own file under docs/team/workspaces/ when it has one, else the default."""
    own = os.path.join(WORKSPACES_DIR, f"{slug.lower()}.md")
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", slug.lower()) and os.path.isfile(own):
        return _read(own)
    return template(DEFAULT)["body"]


def seed(ws: dict, agent: dict) -> bool:
    """Write the workspace's agreement when it has none. True when it wrote one."""
    from . import workspaces
    if workspaces.kb_current_revision(ws, AGREEMENT):
        return False
    try:
        text = agreement_for(ws["slug"])
    except (KeyError, OSError):      # a build without docs/team: the workspace is made all the same, with no agreement
        return False
    own = os.path.isfile(os.path.join(WORKSPACES_DIR, f"{ws['slug'].lower()}.md"))
    workspaces.kb_put(ws, AGREEMENT, text, 0,
                      "seeded: this workspace's own agreement" if own else "seeded from the default team template", [], agent)
    return True


def backfill() -> int:
    """Once per board: every existing workspace with no agreement gets one, written as its owner. The admin
    workspace is not a team. A document a human deleted later is not written again, because this runs once."""
    from . import workspaces
    c = db.conn()
    if c.execute("SELECT 1 FROM registry_meta WHERE key='team_agreements_backfilled'").fetchone():
        return 0
    n = 0
    for row in c.execute("SELECT * FROM workspaces WHERE archived=0 AND slug<>? ORDER BY id", (workspaces.ADMIN,)).fetchall():
        owner = db.get_agent_by_id(row["owner_id"]) if row["owner_id"] else None
        if owner is None:
            continue
        try:
            n += seed(dict(row), owner)
        except (ValueError, workspaces.Conflict, OSError, KeyError):
            continue
    with db._write_lock:
        c.execute("INSERT OR REPLACE INTO registry_meta(key,value) VALUES ('team_agreements_backfilled', ?)", (db.now(),))
        c.commit()
    return n
