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
        self.assertEqual({n: r["plain"] for n, r in rows.items()}, {"FORGEJO_URL": True, "GH_TOKEN": False}, "the list says which is plain")

    def test_an_address_with_credentials_a_query_or_a_fragment_is_refused_when_saved(self):
        # #762: the list shows the address in full, and the board's token goes to it
        for bad in ("http://user:pw@forge.example", "https://forge.example/?token=x", "http://forge.example/#x",
                    "ftp://forge.example", "forge.example", "http://"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    fleet.set_secret("FORGEJO_URL", bad, "jac")
        self.assertEqual([r["name"] for r in fleet.secrets()], [], "nothing was stored")
        fleet.set_secret("FORGEJO_URL", "https://forge.example:3000/sub", "jac")
        self.assertEqual(fleet.forge_url(), "https://forge.example:3000/sub")
        fleet.set_secret("GH_TOKEN", "http://user:pw@not-an-address", "jac")      # any other name takes any value


if __name__ == "__main__":
    unittest.main()
