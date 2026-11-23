"""The rule for a fleet secret's name is spelled in five places (the board, the keeper, bin/bot twice, the
Configuration page's script). They must agree: GH_TOKEN and FORGEJO_URL pass; FORGEJO_URLS, PATH and BOARD do not (#632)."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import fleet

YES, NO = ("GH_TOKEN", "FORGEJO_URL"), ("FORGEJO_URLS", "PATH", "BOARD", "forgejo_url")


def spelled(path: str, pattern: str) -> str:
    m = re.search(pattern, (ROOT / path).read_text())
    assert m, (path, pattern)
    return m.group(1)


class SecretNames(unittest.TestCase):
    def check(self, accepts):
        self.assertEqual([n for n in YES if accepts(n)], list(YES))
        self.assertEqual([n for n in NO if accepts(n)], [])

    def test_the_board(self):
        self.check(lambda n: fleet.SECRET_NAME.fullmatch(n))

    def test_the_keeper(self):
        rx = re.compile(spelled("bin/keeper", r'(?m)^SECRET_NAME = re\.compile\(r"([^"]+)"\)'))
        self.check(lambda n: rx.fullmatch(n))

    def test_bin_bot_reads_fleet_env(self):
        rx = re.compile(spelled("bin/bot", r'm=re\.match\(r"\\s\*\(\?:export\\s\+\)\?\((.+?)\)=\(\.\*\)\$"'))
        self.check(lambda n: rx.fullmatch(n))

    def test_bin_bot_lists_its_own_secrets(self):
        ere = spelled("bin/bot", r"grep -E '(\^.+?\$)'")
        self.check(lambda n: subprocess.run(["grep", "-E", ere], input=n + "\n", text=True, capture_output=True).returncode == 0)

    def test_the_configuration_form(self):
        rx = re.compile(spelled("app/web.py", r"var SECRET_NAME = /\^(.+)\$/;"))
        self.check(lambda n: rx.fullmatch(n))


if __name__ == "__main__":
    unittest.main()
