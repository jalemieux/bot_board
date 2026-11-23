"""The first-run guide's state and the facts it shows (#774, part of #773). One key per human in registry_meta,
`first_run:<handle>`, saved as `pending`, `skipped` or `done`. A missing key means no guide: a board that exists
today shows nothing after an upgrade. The key is made once, `pending`, when the Admin workspace is created at the
first sign-in of a fresh board. The page reads `facts()` instead of assembling them from five endpoints."""
from . import db, fleet, forge, registry, workspaces

STATES = ("pending", "skipped", "done")
KEYLOG = "~/.config/bot_board/bots/{}/keeper.out"      # where a bot that failed to start says why; the design shows this path


def _key(handle: str) -> str:
    return "first_run:" + handle.lower()


def state(handle: str) -> str | None:
    r = db.conn().execute("SELECT value FROM registry_meta WHERE key=?", (_key(handle),)).fetchone()
    return r["value"] if r and r["value"] in STATES else None


def save(handle: str, value: str) -> str:
    """Idempotent. Any of the three states, for the human it is about: the Setup guide link reopens the page with pending."""
    if value not in STATES:
        raise ValueError("state is pending, skipped or done")
    with db._write_lock:
        c = db.conn()
        c.execute("INSERT OR REPLACE INTO registry_meta(key,value) VALUES (?,?)", (_key(handle), value))
        c.commit()
    return value


def start(handle: str) -> None:
    """Once, for the human whose sign-in made the Admin workspace: they see the guide. Never over a state already saved."""
    if state(handle) is None:
        save(handle, "pending")


def _bots(admin_slug: str) -> list[dict]:
    seen, out = set(), []
    for h in fleet.hosts():
        for b in h["bots"]:
            if b.get("workspace") != admin_slug or b["handle"].lower() in seen:
                continue
            seen.add(b["handle"].lower())
            out.append(b)
    return sorted(out, key=lambda b: (b.get("profile") != "admin", b.get("set_at") or "", b["handle"].lower()))


def _role(profile: str | None) -> str:
    try:
        return registry.get_profile(profile)["role"] if profile else ""
    except KeyError:
        return ""


def _bot(b: dict, with_log: bool = False) -> dict:
    st = b.get("state") or {}
    out = {"handle": b["handle"], "harness": b.get("harness") or "", "role": _role(b.get("profile")),
           "state": st.get("state", "unknown"), "words": st.get("words", ""), "reason": st.get("reason", ""), "up": bool(st.get("up"))}
    if with_log and out["state"] == "failing":
        out["log"] = KEYLOG.format(b["handle"])
    return out


def facts(human: dict, address: str) -> dict:
    """What the page shows, from what the board already knows. Nothing here is a secret: the forge's address is an
    address, the sign-in is the human's handle, and the forge's password is not here at all (the page names its file as
    text from the install). The only path is the keeper log's, which the design shows."""
    admin = None
    try:
        admin = workspaces.find(workspaces.ADMIN)
    except KeyError:
        pass
    bots = _bots(workspaces.ADMIN) if admin else []
    steps = {
        "board": {"address": address.rstrip("/"), "handle": human["handle"]},
        "forge": {"configured": True, "url": fleet.forge_url(), "handle": human["handle"]} if forge.configured() else {"configured": False},
        "bot": _bot(bots[0], with_log=True) if bots else None,
        "team": [_bot(b) for b in bots[1:]],
        "general": workspaces.home_slug(admin) if admin else None,
    }
    return {"state": state(human["handle"]), "steps": steps}
