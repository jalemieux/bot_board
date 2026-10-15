"""System status: the fleet, the projects and the routines in one rollup (#92).

Nothing here is stored. It reads what /fleet, /projects and /routines read, and
says in one list what needs a human, each item pointing to where it is fixed.
GET /api/status returns it; /agents shows it above the roster.
"""
from __future__ import annotations

from . import db, fleet, projects

RUNNER_STALE = 15 * 60      # an enabled routine whose runner has not been seen for this long is not running


def _item(text: str, href: str, where: str) -> dict:
    return {"text": text, "href": href, "where": where}


def _fleet(hosts: list, attention: list) -> list:
    out = []
    for h in hosts:
        if not h["reporting"]:
            attention.append(_item(f"{h['name']} is not reporting: last heard {h['reported_at']}",
                                   f"/fleet#box-{h['name']}", "fleet"))
        for c in h.get("credentials") or []:
            if c["state"] in ("missing", "expired") and (c.get("held") or c["kind"] == "login"):
                held = f", {c['held']} bot{'' if c['held'] == 1 else 's'} held" if c.get("held") else ""
                attention.append(_item(f"{c['name']} {c['state']} on {h['name']}{held}"
                                       + (f": {c['detail']}" if c.get("detail") else ""),
                                       f"/fleet#box-{h['name']}", "fleet"))
        bots = []
        for b in h["bots"]:
            up = b["status"].startswith(fleet.UP)
            row = {k: b.get(k) for k in ("handle", "harness", "status", "want", "problem", "project", "pool")}
            row["up"], row["health"] = up, (b.get("health") or "") if up else ""
            bots.append(row)
            why = b.get("problem") or row["health"]
            # a bot nobody asked for and that is simply stopped is not a problem
            if why and (up or b.get("want")):
                attention.append(_item(f"@{b['handle']} on {h['name']}: {why}", f"/fleet#box-{h['name']}", "fleet"))
        out.append({"name": h["name"], "reporting": h["reporting"], "reported_at": h["reported_at"],
                    "load": h.get("load"), "cores": h.get("cores"), "credentials": h.get("credentials") or [],
                    "bots": bots, "up": sum(b["up"] for b in bots)})
    return out


def _admin_project() -> str:
    from . import routines     # loads bin/routines; only when asked
    return routines.ADMIN_PROJECT


def _projects(hosts: list, attention: list) -> list:
    up = {b["handle"].lower() for h in hosts if h["reporting"] for b in h["bots"] if b["status"].startswith(fleet.UP)}
    pool_up = sorted({b["handle"] for h in hosts if h["reporting"] for b in h["bots"]
                      if b.get("pool") and b["status"].startswith(fleet.UP) and not b.get("project")})
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


def rollup() -> dict:
    """{ok, attention: [{text, href, where}], fleet, projects, routines}."""
    attention: list = []
    hosts = fleet.hosts()
    out = {"fleet": _fleet(hosts, attention), "projects": _projects(hosts, attention),
           "routines": _routines(attention)}
    return {"ok": not attention, "attention": attention, **out}
