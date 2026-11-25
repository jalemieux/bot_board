"""The Machines page's script (web.MACHINES_JS, #889) run under node against what /api/fleet answers, so a test reads the table the
page draws. machines(hosts) returns what the script put in the table's slot, the count line, and whether it wired the sort and told the
Mac's card the table was drawn. Skipped by the callers without node."""
import json
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import web

HARNESS = r"""
const slot = {innerHTML: '', textContent: '', querySelector: s => /table\.machines/.test(s) && /<table class="bots machines"/.test(slot.innerHTML) ? {table: true} : null};
const count = {innerHTML: '', textContent: ''};
const log = {sorted: 0, targets: 0, events: [], fetched: []};
global.wireSort = () => { log.sorted++; };
global.showTarget = () => { log.targets++; };
global.location = {hash: %(hash)s};
global.CustomEvent = class { constructor(t) { this.type = t; } };
global.window = {};
global.localStorage = {getItem: () => %(me)s};
global.document = {getElementById: id => id === 'machines-slot' && %(has_slot)s ? slot : null, querySelector: s => s === '.fleet-count' ? count : null,
  dispatchEvent: ev => { log.events.push(ev.type); return true; }};
global.fetch = (u, o) => { log.fetched.push([u, (o && o.headers) || {}]); return %(answer)s; };
"""
TAIL = "\nsetTimeout(() => console.log(JSON.stringify({html: slot.innerHTML, text: slot.textContent, count: count.innerHTML, log})), 20);\n"


def machines(hosts, me=None, ok=True, has_slot=True, hash=''):
    answer = ("Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(%s)})" % json.dumps({"hosts": hosts}) if ok
              else "Promise.resolve({ok: false, status: 500, json: () => Promise.resolve({})})")
    code = (HARNESS % {"me": json.dumps(json.dumps(me)) if me else "null", "answer": answer, "has_slot": "true" if has_slot else "false", "hash": json.dumps(hash)}
            + web.FLEET_ALERTS_JS + web.MACHINES_JS + TAIL)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout)


def drawn(client, headers=None):
    """The table the Machines page draws for what /api/fleet answers this reader."""
    return machines(client.get('/api/fleet', headers=headers or {}).json()['hosts'])['html']
