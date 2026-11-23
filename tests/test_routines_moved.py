"""#820: /routines is gone. The page that answers there sends an old #r=<id> link to its routine's workspace tab (the script, since
the server never sees a fragment), anything else to the workspaces with the note; the top bar has no Routines tab. Skipped without node."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main, web
from app.routines_web import MOVED_JS


def where(hash_, answer):
    """Where the script sends a visitor with this #fragment when the API answers `answer` (None: not found)."""
    code = ("let went = null; const location = {hash: %s, replace: u => { went = u; }};\n"
            "const fetch = () => Promise.resolve({ok: %s, json: () => Promise.resolve(%s)});\n%s\n"
            "setTimeout(() => console.log(went), 20);" % (json.dumps(hash_), json.dumps(answer is not None), json.dumps(answer), MOVED_JS))
    return subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=20).stdout.strip()


class Moved(unittest.TestCase):
    def test_the_top_bar_has_no_routines_tab(self):
        self.assertNotIn("/routines", [h for h, _, _ in web.ADMIN_LINKS])

    def test_the_old_page_answers_and_its_script_sends_a_link_where_the_routine_is(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db.DB_PATH = os.path.join(tmp.name, "board.db")
        db._local = threading.local()
        with TestClient(main.app) as client:
            r = client.get("/routines")
            self.assertEqual(r.status_code, 200)
            self.assertIn("from=routines", r.text)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        if not shutil.which("node"):
            self.skipTest("node is needed")
        self.assertEqual(where("#r=5", {"workspace": "game"}), "/workspaces/game#routines/5")
        self.assertEqual(where("#r=5", None), "/workspaces?from=routines")
        self.assertEqual(where("#approvals", None), "/workspaces/admin#routines")
        self.assertEqual(where("", None), "/workspaces?from=routines")


if __name__ == "__main__":
    unittest.main()
