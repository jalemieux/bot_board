"""#832: deploy/forge-setup passes no secret in an argument list (SECURITY.md, "Passing a secret to a command"). A static check:
the forms that put the invite code, the webhook secret or the admin password in curl's or jq's arguments are not in the file.
Run with python -m unittest discover -s tests."""
import re
import unittest
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1] / "deploy" / "forge-setup").read_text()
CODE = "\n".join(l for l in SCRIPT.splitlines() if not l.lstrip().startswith("#"))


class ForgeSetupSecrets(unittest.TestCase):
    def test_no_curl_takes_a_user_and_password_argument(self):
        self.assertIsNone(re.search(r"curl[^\n]*\s-u\s", CODE), "the admin password goes by curl -K - on standard input")

    def test_jq_gets_the_invite_code_and_the_webhook_secret_from_its_environment(self):
        self.assertIsNone(re.search(r"jq[^\n]*--arg\s+(i|s)\b", CODE))
        self.assertIn("env.I", CODE)
        self.assertIn("env.S", CODE)

    def test_no_curl_has_a_json_body_in_its_arguments_for_those_secrets(self):
        self.assertIsNone(re.search(r"""-d\s+"\$\(\s*(S|H|I)=""", CODE))
        self.assertIsNone(re.search(r"""-d\s+"\$\(jq""", CODE), "a body is sent on standard input or from a descriptor")


if __name__ == "__main__":
    unittest.main()
