"""#557: what a box says about its secrets (secrets_version, secret_collisions) reaches a human, not an unsigned reader or a
bot. Run with python -m unittest discover -s tests."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, fleet, main


class PrivateFields(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.jac = self.register("jac", "human")
        self.keeper = self.register("keeper-box", "keeper")
        self.bot = self.register("ann", "claude-code")
        r = self.client.put("/api/fleet/hosts/box", headers=self.keeper, json={
            "bots": [], "secrets_version": "0123456789abcdef", "secret_collisions": ["GH_TOKEN"]})
        self.assertEqual(r.status_code, 200, r.text)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, "conn"):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post("/api/agents", json={"handle": handle, "kind": kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": "Bearer " + r.json()["token"]}

    def host(self, headers=None):
        r = self.client.get("/api/fleet", headers=headers or {})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["hosts"][0]

    def test_an_unsigned_reader_and_a_bot_do_not_get_them(self):
        for headers in (None, self.bot):
            h = self.host(headers)
            for key in fleet.PRIVATE_REPORT:
                self.assertNotIn(key, h)

    def test_a_human_gets_them(self):
        h = self.host(self.jac)
        self.assertEqual((h["secrets_version"], h["secret_collisions"]), ("0123456789abcdef", ["GH_TOKEN"]))

    def test_the_host_list_drops_them_unless_a_caller_asks_for_them(self):
        """#797: the safe shape is the default of fleet.hosts(), not a step each route remembers."""
        [h] = fleet.hosts()
        for key in fleet.PRIVATE_REPORT:
            self.assertNotIn(key, h)
        [h] = fleet.hosts(private=True)
        self.assertEqual((h["secrets_version"], h["secret_collisions"]), ("0123456789abcdef", ["GH_TOKEN"]))
        [box] = fleet.secret_boxes()          # the one reader that needs them
        self.assertEqual(box["collisions"], ["GH_TOKEN"])


if __name__ == "__main__":
    unittest.main()
