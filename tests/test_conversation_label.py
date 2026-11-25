"""A direct message is labelled by the other people, not by the viewer (#870). `convLabel` is pure JavaScript in the
app script; this runs it under node. Needs node; skipped without it."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest

from app import web

NODE = shutil.which("node")


def label(participants, me):
    m = re.search(r"function convLabel\(c\)\{.*?\n\}", web.APP_JS, re.S)
    assert m, "convLabel is missing"
    script = ("function esc(s){ return String(s); }\nvar ME=%s;\n" % json.dumps(me)) + m.group(0) + \
             "\nprocess.stdout.write(convLabel(%s));" % json.dumps({"slug": "dm-x", "participants": participants})
    with tempfile.NamedTemporaryFile("w", suffix=".js") as fh:
        fh.write(script)
        fh.flush()
        out = subprocess.run([NODE, fh.name], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


@unittest.skipUnless(NODE, "node is not installed")
class ConversationLabel(unittest.TestCase):
    def test_the_viewers_own_name_is_left_out(self):
        self.assertEqual(label(["jac", "lisa"], {"handle": "jac"}), "lisa")
        self.assertEqual(label(["lisa", "jac"], {"handle": "jac"}), "lisa")
        self.assertEqual(label(["jac", "lisa", "ken"], {"handle": "jac"}), "lisa <em>+1</em>")

    def test_a_conversation_the_viewer_is_not_in_keeps_the_first_participant(self):
        self.assertEqual(label(["jac", "lisa"], {"handle": "ken"}), "jac <em>+1</em>")
        self.assertEqual(label(["jac", "lisa"], None), "jac <em>+1</em>")

    def test_a_conversation_of_one_keeps_its_name(self):
        self.assertEqual(label(["jac"], {"handle": "jac"}), "jac")


if __name__ == "__main__":
    unittest.main()
