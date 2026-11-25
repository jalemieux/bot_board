"""The skill, profile and new-profile pages are empty shells whose script reads /api (#860). Their renderers are plain
functions of the data, so a test gets the same data from the API, and runs them under node to see the HTML they make.
`skill_html(client, name)` and friends return that HTML (a fragment: the page's own tags, without the shell)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
STUBS = """
global.localStorage = {getItem: () => null};
global.document = {body: {classList: {add() {}}}, getElementById: () => null};
global.location = {pathname: '/', search: ''};
"""


def _run(js, expression, parts):
    if not shutil.which("node"):
        raise unittest.SkipTest("node is needed")
    script = STUBS + js + f"\nprocess.stdout.write({expression});"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, input=json.dumps(parts))
    assert out.returncode == 0, out.stderr
    return out.stdout


def _get(client, path, headers=None):
    r = client.get(path, headers=headers)
    assert r.status_code == 200, (path, r.status_code, r.text)
    return r


def skill_html(client, name, headers=None):
    from app import registry_web
    url = "/api/skills/" + quote(name)
    parts = [_get(client, url + "?json", headers).json(), _get(client, url + "/history", headers).json(),
             _get(client, "/api/profiles", headers).json(), _get(client, url, headers).text]
    return _run(registry_web.SKILL_PAGE_JS, "skillHtml(composeSkill(JSON.parse(require('fs').readFileSync(0, 'utf8'))))", parts)


def profile_html(client, name, headers=None):
    from app import registry_web
    url = "/api/profiles/" + quote(name)
    parts = [_get(client, url, headers).json(), _get(client, url + "/history", headers).json(), _get(client, "/api/skills", headers).json(),
             _get(client, "/api/fleet", headers).json(), _get(client, "/api/profiles", headers).json(),
             _get(client, "/api/profiles/templates", headers).json()]
    return _run(registry_web.PROFILE_PAGE_JS, "profileHtml(composeProfile(JSON.parse(require('fs').readFileSync(0, 'utf8'))))", parts)


def new_profile_html(client, start=None, headers=None):
    from app import registry_web
    parts = [_get(client, "/api/profiles/templates", headers).json(), _get(client, "/api/profiles", headers).json(),
             _get(client, "/api/skills", headers).json()]
    arg = json.dumps(start)
    return _run(registry_web.NEW_PROFILE_JS, f"newProfileHtml(composeNew(JSON.parse(require('fs').readFileSync(0, 'utf8')), {arg}))", parts)


def new_profile_data(client, start=None, headers=None):
    """The starters, the chosen one and the taken names the new-profile page works from."""
    from app import registry_web
    parts = [_get(client, "/api/profiles/templates", headers).json(), _get(client, "/api/profiles", headers).json(),
             _get(client, "/api/skills", headers).json()]
    arg = json.dumps(start)
    return json.loads(_run(registry_web.NEW_PROFILE_JS, f"JSON.stringify(composeNew(JSON.parse(require('fs').readFileSync(0, 'utf8')), {arg}))", parts))


# A page's script end to end against a document that accepts anything: the loader fetches what the page needs, the
# renderer fills #reg, and the wiring runs on every element it asks for. It catches a misspelt name or a call on the
# wrong data, which no assertion on the HTML would; what the buttons do is for the browser tests.
SMOKE = """
const any = new Proxy(function () {}, {get: (t, k) => (k === Symbol.toPrimitive ? () => '' : any), apply: () => any, set: () => true});
const routes = JSON.parse(require('fs').readFileSync(0, 'utf8'));
let html = null, failed = null;
const reg = {set innerHTML(v) { html = v; }, get innerHTML() { return html; }};
global.localStorage = {getItem: () => JSON.stringify({handle: 'jac', token: 't'})};
global.document = {body: {classList: {add() {}}}, getElementById: (id) => (id === 'reg' ? reg : any), querySelectorAll: () => [], addEventListener() {}, title: ''};
global.location = {pathname: ROUTES_PATH, search: ROUTES_SEARCH};
global.confirm = () => false; global.alert = () => {};
global.fetch = (url) => { if (!(url in routes)) failed = 'no route for ' + url;
  return Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(JSON.parse(routes[url])), text: () => Promise.resolve(routes[url])}); };
process.on('unhandledRejection', (e) => { console.error(e && e.stack || e); process.exit(2); });
%s
setTimeout(() => { if (failed) { console.error(failed); process.exit(3); } process.stdout.write(html || ''); }, 50);
"""


def smoke(js, path, search, routes):
    """The page's script run to the end on `path`; its routes are {url: body text}. Returns the HTML it made."""
    if not shutil.which("node"):
        raise unittest.SkipTest("node is needed")
    script = SMOKE.replace("ROUTES_PATH", json.dumps(path)).replace("ROUTES_SEARCH", json.dumps(search)) % js
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, input=json.dumps(routes))
    assert out.returncode == 0, out.stderr
    return out.stdout


def routes_for(client, urls, headers=None):
    return {u: client.get(u, headers=headers).text for u in urls}
