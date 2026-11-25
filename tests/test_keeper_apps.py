"""The keeper on a Mac reports the apps in /Applications and the handles allowed to run on the host (#595).
One broken app must not stop it reporting. Run with python -m unittest discover -s tests."""
import importlib.machinery
import importlib.util
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_keeper(home):
    os.environ["HOME"] = home
    os.environ["BOT_CONFIG"] = os.path.join(home, "config")
    loader = importlib.machinery.SourceFileLoader("keeper", str(ROOT / "bin" / "keeper"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("keeper", loader))
    loader.exec_module(mod)
    return mod


class MacFacts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ)          # loading the keeper points HOME and BOT_CONFIG at the scratch home: undone after
        env.start()
        self.addCleanup(env.stop)
        self.keeper = load_keeper(self.tmp.name)
        self.apps = Path(self.tmp.name) / "Applications"
        self.apps.mkdir()
        self.keeper.APPS_DIR = str(self.apps)
        self.keeper._apps.update(at=0.0, list=[])

    def bundle(self, name, data):
        d = self.apps / (name + ".app") / "Contents"
        d.mkdir(parents=True)
        (d / "Info.plist").write_bytes(data)

    def test_apps_come_with_their_versions_and_one_broken_bundle_stops_nothing(self):
        self.bundle("Godot", plistlib.dumps({"CFBundleShortVersionString": "4.6.1"}))
        self.bundle("Blender", plistlib.dumps({"CFBundleVersion": "4.5"}, fmt=plistlib.FMT_BINARY))
        self.bundle("Broken", b"<?xml version='1.0'?><plist><dict><key>a</key></plist>")      # malformed XML
        self.bundle("ListShaped", plistlib.dumps(["not", "a", "dict"]))                      # a plist that is not a dict
        (self.apps / "NoPlist.app").mkdir()
        (self.apps / "readme.txt").write_text("not an app")
        got = {a["name"]: a["version"] for a in self.keeper.mac_apps()}
        self.assertEqual(got, {"Godot": "4.6.1", "Blender": "4.5", "Broken": "", "ListShaped": "", "NoPlist": ""})

    def test_the_handles_the_owner_listed_are_reported_and_an_absent_file_lists_none(self):
        consent = Path(self.tmp.name) / "host-bots-allowed"
        self.keeper.HOST_CONSENT = str(consent)
        self.assertEqual(self.keeper.host_allowed(), [])
        consent.write_text("Mira\n\n  gwen  \n")
        self.assertEqual(self.keeper.host_allowed(), ["gwen", "mira"])

    def test_a_box_that_is_not_a_mac_reports_the_handles_it_listed_and_no_apps(self):
        # #876: a bot may run on the host of any box that chose containers, so every box reports its consent lines; only a Mac has apps
        consent = Path(self.tmp.name) / "host-bots-allowed"
        consent.write_text("Mira\n")
        self.keeper.HOST_CONSENT = str(consent)
        from unittest import mock
        with mock.patch.object(self.keeper.sys, "platform", "linux"):
            self.assertEqual(self.keeper.host_facts(), {"host_allowed": ["mira"]})
        with mock.patch.object(self.keeper.sys, "platform", "darwin"), mock.patch.object(self.keeper, "mac_apps", lambda: [{"name": "Godot", "version": "4"}]):
            self.assertEqual(self.keeper.host_facts(), {"host_allowed": ["mira"], "apps": [{"name": "Godot", "version": "4"}]})


if __name__ == "__main__":
    unittest.main()
