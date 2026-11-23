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
                    "ftp://forge.example", "forge.example", "http://", "http://[::1"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    fleet.set_secret("FORGEJO_URL", bad, "jac")
        self.assertEqual([r["name"] for r in fleet.secrets()], [], "nothing was stored")
        fleet.set_secret("FORGEJO_URL", "https://forge.example:3000/sub", "jac")
        self.assertEqual(fleet.forge_url(), "https://forge.example:3000/sub")
        fleet.set_secret("GH_TOKEN", "http://user:pw@not-an-address", "jac")      # any other name takes any value

    def test_an_address_stored_with_credentials_before_the_rule_is_not_shown_in_full(self):
        c = db.conn()
        c.execute("INSERT INTO fleet_secrets (name, value, set_by, set_at) VALUES ('FORGEJO_URL', 'http://u:pw@forge.example', 'jac', ?)", (db.now(),))
        c.commit()
        row = fleet.secrets()[0]
        self.assertTrue(row["plain"])
        self.assertNotIn("value", row)
        self.assertNotIn("u:pw", str(fleet.secrets()))

    def report(self, host, **fields):
        k = db.get_agent_by_handle("keeper-" + host) or db.register_agent("keeper-" + host, "keeper", "", None, False)[0]
        self.assertTrue(fleet.report(host, k["id"], {"bots": [], **fields}))

    def test_each_box_says_whether_it_has_the_current_set_and_what_it_also_holds(self):
        # #557: received is true on the board's version, false on an older one, unknown when the keeper does not say;
        # collisions are names only
        fleet.set_secret("GH_TOKEN", "ghp_abcdefgh1234", "jac")
        now = fleet.secrets_version()
        self.report("a", secrets_version=now, secret_collisions=["GH_TOKEN", "not a name", "PATH"])
        self.report("b", secrets_version="0123456789abcdef")
        self.report("c")
        boxes = {b["host"]: b for b in fleet.secret_boxes()}
        self.assertEqual((boxes["a"]["received"], boxes["b"]["received"], boxes["c"]["received"]), (True, False, None))
        self.assertEqual(boxes["a"]["collisions"], ["GH_TOKEN"], "only names a secret may have, never a value")
        self.assertEqual(boxes["b"]["collisions"], [])
        fleet.set_secret("GH_TOKEN", "ghp_replaced00000", "jac")      # the set changed: a box on the old one is behind
        self.assertFalse({b["host"]: b for b in fleet.secret_boxes()}["a"]["received"])

    def test_a_box_that_stopped_reporting_is_unknown_not_behind(self):
        fleet.set_secret("GH_TOKEN", "ghp_abcdefgh1234", "jac")
        self.report("a", secrets_version=fleet.secrets_version())
        db.conn().execute("UPDATE fleet_hosts SET reported_at='2020-01-01T00:00:00+00:00'")
        db.conn().commit()
        self.assertIsNone(fleet.secret_boxes()[0]["received"])


if __name__ == "__main__":
    unittest.main()
