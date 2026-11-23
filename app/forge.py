"""The forge client (#715): create a repository, check that one exists, nothing else. It is how a workspace gets its
repository (docs/workspace-repository.md). The board holds a token that can write, so the client is kept narrow:
- the token goes only to the forge's own address (FORGEJO_URL), over http or https, never with credentials in the URL;
- no redirect is followed, so a forge answer cannot send the token elsewhere;
- a timeout and a size cap bound every call;
- `owner/name` is validated strictly and each part quoted, so a crafted name cannot reach another endpoint with the token;
- only two calls exist: create under the token's own account, and get one repository."""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import fleet, workspaces

OWNER = "fleet"             # the account the bots' token belongs to: a new repository is fleet/<slug>
TIMEOUT = 10                # seconds, for the whole call
MAX_BYTES = 1_000_000       # a forge answer larger than this is refused
_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")      # starts with a letter or digit: "." and ".." cannot match


class ForgeError(Exception):
    """Why the forge could not be asked or did not do it, in words a manager can read."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None     # the 3xx is raised as an HTTPError below, and no header is sent on


# ProxyHandler({}): no HTTP_PROXY or HTTPS_PROXY from the board's environment, so the token goes to the forge and nowhere else
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
_make = threading.Lock()      # one board process: a double click or the first box's report cannot both create


def valid_name(part: str) -> str:
    if not isinstance(part, str) or not _PART.fullmatch(part) or part.lower().endswith(".git"):
        raise ForgeError(f"{part!r} is not a name the forge client will send: letters, digits, dot, dash and underscore, "
                         "starting with a letter or digit")
    return part


def _base() -> str:
    url = fleet.forge_url().rstrip("/")
    u = urllib.parse.urlsplit(url)
    if not url:
        raise ForgeError("no forge address is set: add FORGEJO_URL under Secrets on the Configuration page")
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ForgeError("FORGEJO_URL must be an http or https address with no user, password, query or fragment")
    return url


def _call(method: str, path: str, body: dict | None = None, read: bool = False, timeout: float | None = None):
    """(status, parsed JSON or None). Raises ForgeError when the forge cannot be reached or answers too much."""
    base = _base()
    token = fleet.forge_token(read=read)
    if not token:
        raise ForgeError("no forge token is set: add FORGEJO_TOKEN under Secrets on the Configuration page")
    req = urllib.request.Request(base + path, method=method, headers={"Authorization": "token " + token, "Accept": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _opener.open(req, timeout=timeout or TIMEOUT) as r:
            status, raw = r.status, r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(MAX_BYTES + 1)
        if 300 <= status < 400:
            raise ForgeError(f"the forge answered with a redirect ({status}); the client does not follow one with its token") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ForgeError(f"the forge did not answer: {getattr(exc, 'reason', exc)}") from exc
    if len(raw) > MAX_BYTES:
        raise ForgeError("the forge's answer was larger than the client accepts")
    try:
        return status, (json.loads(raw.decode("utf-8")) if raw.strip() else None)
    except (UnicodeDecodeError, ValueError):
        return status, None


def get_repository(owner: str, name: str, read: bool = False, timeout: float | None = None) -> dict | None:
    """{"full_name", "owner", "empty"} for a repository, None when the forge has none by that name."""
    path = "/api/v1/repos/%s/%s" % (urllib.parse.quote(valid_name(owner), safe=""), urllib.parse.quote(valid_name(name), safe=""))
    status, data = _call("GET", path, read=read, timeout=timeout)
    if status == 404:
        return None
    if status != 200 or not isinstance(data, dict):
        raise ForgeError(f"the forge answered {status} when asked for {owner}/{name}")
    return {"full_name": str(data.get("full_name") or f"{owner}/{name}"),
            "owner": str((data.get("owner") or {}).get("login") or ""), "empty": bool(data.get("empty"))}


def create_repository(name: str, timeout: float | None = None) -> str:
    """Create a private `name` under the token's own account, empty, and return owner/name. Private on purpose: the bots and
    the site's administrators see it, and a public repository is a person's decision. The account is the token's; it is `fleet`
    on every board made by deploy/forge-setup, which is what make_repository looks for first."""
    status, data = _call("POST", "/api/v1/user/repos", {"name": valid_name(name), "private": True, "auto_init": False}, timeout=timeout)
    if status not in (200, 201) or not isinstance(data, dict):
        raise ForgeError(f"the forge answered {status} when asked to create {name}")
    return str(data.get("full_name") or f"{OWNER}/{name}")


def make_repository(slug: str, timeout: float | None = None) -> str:
    """fleet/<slug>: created when the forge has none, adopted when it has one that is empty and owned by `fleet`, and
    refused, with the reason, when it has content or another owner. Nothing is overwritten. Returns owner/name."""
    existing = get_repository(OWNER, slug, timeout=timeout)
    if existing is None:
        return create_repository(slug, timeout=timeout)
    if existing["owner"].lower() != OWNER:
        raise ForgeError(f"{OWNER}/{slug} exists on the forge and belongs to {existing['owner'] or 'someone else'}")
    if not existing["empty"]:
        raise ForgeError(f"{OWNER}/{slug} exists on the forge and has content: link it instead of creating it")
    return existing["full_name"]


def ensure_repository(slug: str, agent: dict, timeout: float | None = None) -> dict:
    """The workspace's repository (#715), made when it has none. {"repository": owner/name, "reason": ""} once it has one;
    {"repository": "", "reason": why} when the forge could not make or adopt one, so the workspace goes on without. The
    only creator: it is called from workspace creation, the Team tab's button and the first box's report, and the
    installer never makes one. It checks who may link before it asks the forge for anything."""
    ws = workspaces.find(slug)
    if not workspaces.repository(ws) and not (workspaces.can_manage(ws, agent) or workspaces.is_admin_bot(agent)):
        raise PermissionError("Only a human or an admin bot can create a repository for a workspace.")
    with _make:        # read the workspace again inside the lock: the caller before us may have made it
        ws = workspaces.find(slug)
        have = workspaces.repository(ws)
        if have:
            return {"repository": have, "reason": ""}
        if ws["archived"]:
            raise workspaces.Conflict("Archived workspaces are read-only.")
        try:
            full = make_repository(ws["slug"], timeout=timeout)
            workspaces.set_repository(ws["slug"], full, agent)
        except (ForgeError, workspaces.Conflict) as exc:
            return {"repository": "", "reason": str(exc)}
    return {"repository": full, "reason": ""}
