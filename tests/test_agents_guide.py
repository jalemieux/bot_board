"""One agent guide covers collaboration and protocol."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import main, web


class AgentGuide(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, base_url="http://fleet.example:8080")

    def tearDown(self):
        self.client.close()

    def test_guide_serves_the_single_source_with_request_address(self):
        source = Path(main._ROOT, "AGENTS.md").read_text(encoding="utf-8")
        response = self.client.get("/agents.md")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, source.replace(
            "http://127.0.0.1:8080", "http://fleet.example:8080"))
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertIn("One thread has one worker.", response.text)
        self.assertIn("The board is the only communication channel between fleet agents.", response.text)
        self.assertIn("use harness-native messaging, teammate chats, or local cross-session channels", response.text)
        self.assertIn("Authorization: Bearer $BOARD_TOKEN", response.text)
        self.assertNotIn("llms.txt", response.text)

    def test_retired_protocol_endpoint_is_removed(self):
        self.assertEqual(self.client.get("/llms.txt").status_code, 404)

    def test_discovery_and_onboarding_point_to_the_single_guide(self):
        page = web.onboard_page("Fleet", "http://fleet.example:8080", False)
        self.assertIn('href="/agents.md"', page)
        self.assertIn("The fleet launcher handles pickup, delivery and waiting", page)
        self.assertNotIn("llms.txt", page)
        self.assertNotIn("Standing by for work", page)
        self.assertIn("GET /agents.md", main.API_MAP["endpoints"])
