"""The admin pages of a throwaway board, seeded with a fleet in every state, as screenshots (#94).

    pip install playwright httpx && playwright install chromium
    python tests/ux_shots.py . /tmp/shots        # KEEP=1 leaves the board running to click around
"""
import json, os, socket, sqlite3, subprocess, sys, tempfile, time
import httpx
from playwright.sync_api import sync_playwright

ROOT, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
BASE = f"http://127.0.0.1:{PORT}"
env = {**os.environ, "BOT_BOARD_DB": f"{tmp}/board.db", "BOT_BOARD_PROJECT_ADMIN_KEY": "k",
       "ROUTINES_HANDLE": "routines", "HOME": tmp, "BOT_CONFIG": f"{tmp}/config"}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)], cwd=ROOT, env=env,
                       stdout=open(f"{tmp}/log", "w"), stderr=subprocess.STDOUT)
c = httpx.Client(base_url=BASE, timeout=10)
for _ in range(100):
    try:
        c.get("/api"); break
    except httpx.HTTPError:
        time.sleep(0.1)


def ok(r):
    assert r.status_code < 300, (r.request.method, r.request.url, r.status_code, r.text)
    return r.json()


def reg(handle, kind, desc=""):
    tok = ok(c.post("/api/agents", json={"handle": handle, "kind": kind, "description": desc}))["token"]
    return {"Authorization": "Bearer " + tok}, tok


op, op_tok = reg("jac", "human", "operator")
ok(c.post("/api/workspaces/access", headers=op, json={"key": "k"}))
bots = {}
for h, d in [("arlo", "standby pool on alpha"), ("cora", "standby pool on alpha"), ("dex", "standby pool on beta"),
             ("esme", "standby pool on beta"), ("ezra", "standby pool on beta"), ("kai", "standby pool on alpha"),
             ("alpha-wordsnap-3c1e", "refactoring ingest in wordsnap"), ("routines", "routines runner")]:
    bots[h] = reg(h, "claude-code", d)[0]
for h in ("arlo", "dex", "esme"):
    ok(c.post("/api/messages", headers=bots[h], json={"channel": "help", "body": f"{h} was here"}))

for slug, name in [("wordsnap", "WordSnap"), ("space_game", "A space game")]:
    ok(c.post("/api/workspaces", headers=op, json={"slug": slug, "name": name, "purpose": f"{name}: what it is for.",
               "seed": {"description": f"{name}: the design notes.", "links": [f"https://github.com/example/{slug}"]}}))
for g in ("economy", "ship-design", "lore"):
    ok(c.post("/api/workspaces/space_game/channels", headers=op, json={"slug": g, "name": g.title(), "description": f"{g} of the game",
              "focus": f"Keep {g} consistent with the knowledge base.", "references": [{"path": f"{g}/INDEX.md"}]}))
ok(c.put("/api/workspaces/space_game/knowledge/INDEX.md", headers=op, json={"base_revision": 0, "body": "# Space game\n\nThe game is about trade.",
         "reason": "seed", "sources": ["README.md"]}))

now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
hour = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 3600))
day = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 86400 * 3))


def bot(handle, status, harness="claude", pool=True, health="", last_run=hour):
    return {"handle": handle, "harness": harness, "status": status, "serves": "/home/op/bots_sandbox" if pool else "",
            "pool": pool, "last_run": last_run, "health": health}


ok_creds = [{"name": "gh", "kind": "secret", "state": "ok", "detail": "", "held": 0},
            {"name": "claude", "kind": "login", "state": "ok", "detail": "", "held": 0}]
boxes = {
    "alpha": dict(cores=16, load=11.8, mem_total_mb=16000, mem_free_mb=1200, os="Linux 6.8.0", credentials=ok_creds,
                  bots=[bot("arlo", "running space_game"), bot("cora", "waiting"), bot("kai", "running admin, stopping"),
                        bot("alpha-wordsnap-3c1e", "running", pool=False),
                        bot("ivo", "waiting", health="last run failed (exit 1) at 2026-10-15T00:33:00Z: Error: rate limited"),
                        bot("nell", "stopped", harness="", last_run=day), bot("otto", "stopped", harness="", last_run="")]),
    "beta": dict(cores=8, load=0.4, mem_total_mb=8000, mem_free_mb=6000, os="Darwin 25.5.0",
                 credentials=[{"name": "gh", "kind": "secret", "state": "ok", "detail": "", "held": 0},
                              {"name": "claude", "kind": "login", "state": "missing", "detail": "not logged in", "held": 2}],
                 bots=[bot("dex", "waiting", health="logged out of claude since 2026-10-15T01:00:00Z: run `claude /login` on beta; messages are kept until then"),
                       bot("esme", "waiting", health="out of claude usage since 2026-10-15T02:00:00Z: 5-hour limit reached; messages are kept, retried every 10 min"),
                       bot("ezra", "running economy"), bot("tess", "stopped", harness="codex", last_run=day)]),
    "gamma": dict(cores=4, load=None, mem_total_mb=None, mem_free_mb=None, os="Linux 6.1", credentials=[],
                  bots=[bot("wes", "waiting"), bot("zoe", "stopped")]),
}
keepers = {}
for name, rep in boxes.items():
    keepers[name] = reg(f"keeper-{name}", "keeper")[0]
    ok(c.put(f"/api/fleet/hosts/{name}", headers=keepers[name], json=rep))
ok(c.put("/api/workspaces/wordsnap/bots", headers=op, json={"bots": ["cora"]}))
ok(c.put("/api/workspaces/admin/bots", headers=op, json={"bots": ["kai"]}))
# requests from the human: a stop, a kill, a start that has not happened, one that failed, a move
ok(c.put("/api/fleet/bots/cora", headers=op, json={"host": "alpha", "run": False}))
ok(c.put("/api/fleet/bots/ivo", headers=op, json={"host": "alpha", "run": False, "kill": True}))
ok(c.put("/api/fleet/bots/nell", headers=op, json={"host": "alpha", "run": True, "harness": "claude", "workspace": "wordsnap"}))
ok(c.put("/api/fleet/bots/tess", headers=op, json={"host": "beta", "run": True, "workspace": "space_game"}))
ok(c.post("/api/fleet/bots/ezra/move", headers=op, json={"to": "alpha"}))
ok(c.put("/api/fleet/hosts/beta", headers=keepers["beta"], json={**boxes["beta"],
         "problems": {"tess": "codex is not installed (or not on the keeper's PATH)"}}))
ok(c.put("/api/fleet/secrets/GH_TOKEN", headers=op, json={"value": "ghp_example1234"}))
# gamma went dark three days ago
db = sqlite3.connect(f"{tmp}/board.db")
db.execute("UPDATE fleet_hosts SET reported_at=? WHERE name='gamma'", (day,)); db.commit()

# routines: one paused, one enabled, one failed
r1 = ok(c.post("/api/routines", headers=op, json={"name": "wordsnap-issues", "recipe": {"type": "github-issues",
            "repo": "example/wordsnap", "workspace": "wordsnap"}, "every": 300}))
r2 = ok(c.post("/api/routines", headers=op, json={"name": "nightly-audit", "channel": "space_game",
            "check": "echo '{\"items\": []}'", "prompt": "Audit the repo.", "every": 86400, "about": "a nightly audit"}))
r3 = ok(c.post("/api/routines", headers=op, json={"name": "stale-prs", "channel": "wordsnap",
            "check": "gh pr list --json number", "prompt": "Ping owners of stale PRs.", "every": 3600}))
rid = lambda r: (r.get("routine") or r)["id"]
ok(c.post(f"/api/routines/{rid(r2)}/enable", headers=op))
ok(c.post(f"/api/routines/{rid(r3)}/enable", headers=op))
ok(c.post(f"/api/routines/{rid(r3)}/report", headers=bots["routines"], json={"failed": "check exited 1: gh: not logged in"}))

PAGES = ["/fleet", "/workspaces", "/workspaces/space_game", "/workspaces/wordsnap", "/routines", "/agents"]
with sync_playwright() as p:
    b = p.chromium.launch()
    for signed in (False, True):
        for w, tag in ((1400, "wide"), (420, "narrow")):
            ctx = b.new_context(viewport={"width": w, "height": 900})
            if signed:
                ctx.add_init_script(f"localStorage.setItem('bb.me', JSON.stringify({{handle:'jac', token:'{op_tok}'}}))")
            pg = ctx.new_page()
            for path in PAGES:
                pg.goto(BASE + path); pg.wait_for_load_state("networkidle"); time.sleep(0.4)
                fn = f"{OUT}/{path.strip('/').replace('/', '_')}-{tag}-{'in' if signed else 'out'}.png"
                pg.screenshot(path=fn, full_page=True)
            ctx.close()
    b.close()
print(BASE, tmp)
if os.environ.get("KEEP"):
    print("serving; ctrl-c to stop"); srv.wait()
srv.terminate()
