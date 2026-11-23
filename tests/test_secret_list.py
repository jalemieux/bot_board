"""#701: the secrets list shows FORGEJO_URL in full, since it is an address and not a secret, and nothing else's value.
Run with python -m unittest discover -s tests."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, fleet


class SecretList(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "board.db")
        db._local = threading.local()
        db.init()
        fleet.init()
        self.addCleanup(self.tmp.cleanup)

    def test_only_the_forge_address_carries_its_value(self):
        fleet.set_secret("FORGEJO_URL", "http://forge.example:3000", "jac")
        fleet.set_secret("GH_TOKEN", "ghp_abcdefgh1234", "jac")
        rows = {r["name"]: r for r in fleet.secrets()}
        self.assertEqual(rows["FORGEJO_URL"]["value"], "http://forge.example:3000")
        self.assertNotIn("value", rows["GH_TOKEN"])
        self.assertEqual(rows["GH_TOKEN"]["ends"], "1234")
        self.assertNotIn("ghp_abcdefgh", str(fleet.secrets()))


if __name__ == "__main__":
    unittest.main()
