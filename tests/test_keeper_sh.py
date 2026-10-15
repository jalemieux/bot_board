"""The board serves keeper.sh filled in, and /fleet shows the line. Run with python -m unittest discover -s tests."""
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, fleet, main


class KeeperSh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        db.init()
        fleet.init()
        self.client = TestClient(main.app, base_url='http://minipc-1.example.ts.net:8080')

    def tearDown(self):
        self.tmp.cleanup()

    def test_fills_in_the_board_it_was_fetched_from(self):
        r = self.client.get('/keeper.sh')
        self.assertEqual(r.status_code, 200)
        self.assertIn('BOARD="${BOARD:-http://minipc-1.example.ts.net:8080}"\n', r.text)
        self.assertNotIn('@@BOARD_FILLS_IN@@', r.text)
        self.assertNotIn('KEEPER_OPERATORS="${', r.text)

    def test_fills_in_the_operators(self):
        r = self.client.get('/keeper.sh', params={'op': 'jac,billy'})
        self.assertIn('KEEPER_OPERATORS="${KEEPER_OPERATORS:-jac billy}"\n', r.text)

    def test_refuses_an_operator_that_is_not_a_handle(self):
        for op in ('jac"; rm -rf ~; "', '$(id)', 'a'):
            self.assertEqual(self.client.get('/keeper.sh', params={'op': op}).status_code, 400, op)

    def test_refuses_a_host_header_that_could_break_out(self):
        r = self.client.get('/keeper.sh', headers={'host': 'x$(id)'})
        self.assertEqual(r.status_code, 400)

    def test_served_script_is_valid_bash(self):
        r = self.client.get('/keeper.sh', params={'op': 'jac'})
        self.assertEqual(subprocess.run(['bash', '-n'], input=r.text, text=True).returncode, 0)

    def test_environment_still_wins(self):
        script = self.client.get('/keeper.sh', params={'op': 'jac'}).text
        head = script.split('\nCMD=')[0] + '\necho "$BOARD|$KEEPER_OPERATORS"\n'
        out = subprocess.run(['bash'], input=head, text=True, capture_output=True,
                             env={**os.environ, 'BOARD': 'http://other:9000', 'KEEPER_OPERATORS': 'ada'})
        self.assertEqual(out.stdout.strip(), 'http://other:9000|ada')

    def test_fleet_page_shows_the_line(self):
        page = self.client.get('/fleet').text
        self.assertIn('id="addbox"', page)
        self.assertIn("curl -fsSL 'http://minipc-1.example.ts.net:8080/keeper.sh' | bash", page)


if __name__ == '__main__':
    unittest.main()
