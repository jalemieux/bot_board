"""System status: the fleet, the projects and the routines in one rollup (#92).

Nothing here is stored. It reads what /fleet, /projects and /routines read, and
says in one list what needs a human, each item pointing to where it is fixed.
GET /api/status returns it; /agents shows it above the roster.
"""
from __future__ import annotations

from . import db, fleet, projects
from .web import ago

RUNNER_STALE = 15 * 60      # an enabled routine whose runner has not been seen for this long is not running


def _item(text: str, href: str, where: str) -> dict:
    return {"text": text, "href": href, "where": where}


def _fleet(hosts: list, attention: list) -> list:
    out = []
    for h in hosts:
        if not h["reporting"]:
            attention.append(_item(f"{h['name']} is not reporting: last heard {ago(h['reported_at'])}",
                                   f"/fleet#box-{h['name']}", "fleet"))
        # a login missing here is one item, not one more per bot it holds
        out_of = {c["name"] for c in h.get("credentials") or [] if c["kind"] == "login" and c["state"] != "ok"}
        for c in h.get("credentials") or []:
            if c["state"] in ("missing", "expired") and (c.get("held") or c["kind"] == "login"):
                held = f", {c['held']} bot{'' if c['held'] == 1 else 's'} held" if c.get("held") else ""
                attention.append(_item(f"{c['name']} {c['state']} on {h['name']}{held}"
                                       + (f": {c['detail']}" if c.get("detail") else ""),
                                       f"/fleet#box-{h['name']}", "fleet"))
        bots = []
        for b in h["bots"]:
            st = b.get("state") or fleet.bot_state(b, h["reporting"])
            row = {k: b.get(k) for k in ("handle", "harness", "status", "want", "problem", "project", "pool")}
            row.update(state=st, up=st["can_work"], health=b.get("health") or "")
            bots.append(row)
            why = b.get("problem") or (row["health"] if b["status"].startswith(fleet.UP) else "")
            # a bot nobody asked for and that is simply stopped is not a problem; neither is one on a
            # box gone dark (the box's own item covers it) or one held by a login the box lacks (ditto)
            held = st["state"] == "needs login" and (b.get("harness") or "") in out_of
            if why and h["reporting"] and not held and (b["status"].startswith(fleet.UP) or b.get("want")):
                attention.append(_item(f"@{b['handle']} on {h['name']}: {why}", f"/fleet#box-{h['name']}", "fleet"))
        out.append({"name": h["name"], "reporting": h["reporting"], "reported_at": h["reported_at"],
                    "load": h.get("load"), "cores": h.get("cores"), "credentials": h.get("credentials") or [],
                    "bots": bots, "up": sum(b["up"] for b in bots)})
    return out


def _admin_project() -> str:
    from . import routines     # loads bin/routines; only when asked
    return routines.ADMIN_PROJECT


def _projects(hosts: list, attention: list) -> list:
    # up: can take work now (#100); a logged-out bot on a reporting box is not up
    can = lambda h, b: (b.get("state") or fleet.bot_state(b, h["reporting"]))["can_work"]
    up = {b["handle"].lower() for h in hosts for b in h["bots"] if can(h, b)}
    pool_up = sorted({b["handle"] for h in hosts for b in h["bots"]
                      if b.get("pool") and can(h, b) and not b.get("project")})
    out = []
    for p in db.conn().execute("SELECT * FROM projects WHERE archived=0 ORDER BY slug").fetchall():
        p = dict(p)
        pool = projects.pool_settings(p)
        channel = db.get_board_by_id(p["board_id"])["slug"]
        if pool["configured"]:
            working = [b for b in pool["bots"] if b.lower() in up]
            if not working:
                attention.append(_item(f"project {p['slug']}: none of its pool bots is up"
                                       + (f" ({', '.join('@' + b for b in pool['bots'])})" if pool["bots"] else
                                          " (its pool is empty)"), f"/projects/{p['slug']}", "projects"))
        else:
            working = pool_up      # a project without its own pool shares the free pool bots
            if p["slug"] == _admin_project():
                # routines need a bot of their own here: a shared pool bot is not an admin bot (#103)
                attention.append(_item(f"project {p['slug']} has no admin bot: dedicate a pool bot to it, so "
                                       "requests for routines are answered", f"/projects/{p['slug']}", "projects"))
        out.append({"slug": p["slug"], "name": p["name"], "channel": channel, "dedicated": pool["configured"],
                    "bots": pool["bots"] if pool["configured"] else [], "up": working,
                    "readiness": projects.readiness(p["board_id"])})
    return out


def _routines(attention: list) -> list:
    from . import routines     # loads bin/routines; only when asked
    try:
        rows = routines.list_routines(full=True)
    except db.sqlite3.OperationalError:     # a board without routines yet
        return []
    runner = db.get_agent_by_handle(routines.cli.ME)
    seen = db.seconds_since(runner["last_seen_at"]) if runner and runner.get("last_seen_at") else None
    out = []
    for r in rows:
        last = r["jobs"][0] if r.get("jobs") else None
        row = {"id": r["id"], "name": r["name"], "state": r["state"], "channel": r["channel"],
               "every": r.get("every"), "failure": r.get("failure"), "problem": r.get("problem") or "",
               "last_job": last and {"at": last["created_at"], "done_by": last["done_by"]},
               "last_test": r.get("last_test") and {"ok": r["last_test"].get("ok"), "at": r["last_test"].get("at")}}
        href = f"/routines#r={r['id']}"
        if r["state"] == "failed":
            attention.append(_item(f"routine {r['name']} failed: {r.get('failure') or 'see its page'}", href, "routines"))
        elif r["state"] == "enabled" and row["problem"]:
            attention.append(_item(f"routine {r['name']}: {row['problem']}", href, "routines"))
        out.append(row)
    stalled = [r["name"] for r in out if r["state"] == "enabled"]
    if stalled and (seen is None or seen > RUNNER_STALE):    # one item: it is the runner that is down
        attention.append(_item(f"{len(stalled)} enabled routine(s) ({', '.join(stalled)}) but the runner "
                               f"(@{routines.cli.ME}) " + ("has never connected" if seen is None
                                                          else f"was last seen {int(seen // 60)} min ago"),
                               "/routines", "routines"))
    return out


def rollup(hosts: list | None = None) -> dict:
    """{ok, attention: [{text, href, where}], fleet, projects, routines}. hosts: fleet.hosts(),
    when the caller has it already (each call scans the messages for last posts)."""
    attention: list = []
    hosts = fleet.hosts() if hosts is None else hosts
    out = {"fleet": _fleet(hosts, attention), "projects": _projects(hosts, attention),
           "routines": _routines(attention)}
    return {"ok": not attention, "attention": attention, **out}
