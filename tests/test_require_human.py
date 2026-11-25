"""#840: "only a human" is asked in one place, workspaces.require_human, and a refusal is a 403 with the same words as before.
Run with python -m unittest discover -s tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workspaces import Base                    # noqa: E402
from app import workspaces                          # noqa: E402


class RequireHuman(Base):
    def setUp(self):
        super().setUp()
        self.human = self.register('jac', 'human')
        self.bot = self.register('ada', 'claude-code')
        self.keeper = self.register('keeper-box', 'keeper')

    def refused(self, method, path, words, auth=None, data=None):
        r = self.client.request(method, path, json=data, headers=auth or self.bot)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()['error'], words)

    def test_a_bot_is_refused_with_the_words_it_always_got(self):
        self.refused('PUT', '/api/hooks/forgejo', "only a human sets a hook's secret", data={'secret': 'x' * 20})
        self.refused('PUT', '/api/fleet/secrets/GH_TOKEN', "only a human manages the fleet's secrets", data={'value': 'v'})
        self.refused('POST', '/api/status/dismissed', 'only a human dismisses alerts', data={'key': 'k'})
        self.refused('POST', '/api/signin-links', 'only a human signs a browser in')
        self.refused('GET', '/api/first-run', 'the first-run guide is for a person')

    def test_a_human_is_not_refused_by_them(self):
        r = self.client.put('/api/hooks/forgejo', json={'secret': 'x' * 20}, headers=self.human)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post('/api/status/dismissed', json={'key': 'k'}, headers=self.human)
        self.assertNotEqual(r.status_code, 403, r.text)

    def test_the_helper_lets_a_human_or_an_exception_through_and_says_no_to_the_rest(self):
        human = {'kind': 'human'}
        bot = {'kind': 'claude-code'}
        workspaces.require_human(human, 'no')
        workspaces.require_human(bot, 'no', unless=True)
        with self.assertRaises(PermissionError) as ctx:
            workspaces.require_human(bot, 'only a human, thank you')
        self.assertEqual(str(ctx.exception), 'only a human, thank you')
        with self.assertRaises(PermissionError):
            workspaces.require_human(None, 'no')

    def test_a_keeper_may_take_its_own_box_off_the_fleet_and_another_may_not(self):
        self.client.put('/api/fleet/hosts/box', headers=self.keeper, json={'bots': []})
        other = self.register('keeper-other', 'keeper')
        r = self.client.delete('/api/fleet/hosts/box', headers=other)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()['error'], "only a human, or the box's own keeper, takes a box off the fleet")
        r = self.client.delete('/api/fleet/hosts/box', headers=self.keeper)
        self.assertEqual(r.status_code, 200, r.text)

    def test_no_route_asks_for_a_human_by_hand_any_more(self):
        source = (Path(__file__).resolve().parents[1] / 'app' / 'main.py').read_text()
        self.assertNotIn('agent["kind"] != "human"', source)


if __name__ == '__main__':
    unittest.main()
