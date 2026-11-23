"""A helper for tests that start from a board with only an Admin workspace: mark the first run (#650) done, so the first human does not get a General workspace."""
from app import db


def skip_first_run() -> None:
    db.conn().execute("INSERT OR REPLACE INTO registry_meta(key,value) VALUES ('first_run_workspace','x')")
    db.conn().commit()
