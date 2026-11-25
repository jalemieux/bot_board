"""A helper for tests that start from a board with only an Admin workspace: mark the first run (#650) done, so the first human does not get a General workspace."""
from app import db


def skip_first_run() -> None:
    db.conn().execute("INSERT OR REPLACE INTO registry_meta(key,value) VALUES ('first_run_workspace','x')")
    db.conn().commit()


def signed_in(identity: dict, scope: str | None = None, extra: str = "") -> str:
    """The init script of a browser test that starts signed in as a human: the session flag the first-run guide checks
    (so the guide stays out of the way), the identity, and, when given, the workspace the page opens on. `extra` is
    more script to run after them."""
    import json
    script = "sessionStorage.setItem('bb.guide', '1');"
    if scope:
        script += "localStorage.setItem('bb.scope', " + json.dumps(json.dumps(scope)) + ");"
    return script + "localStorage.setItem('bb.me', " + json.dumps(json.dumps(identity)) + ");" + extra
