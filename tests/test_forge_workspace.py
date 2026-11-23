"""#715: a workspace gets its repository when it is made, from the Team tab's button, and best effort: no forge address,
no token or an answer that says no leaves the workspace made, with the reason. A local stand-in plays Forgejo.
Run with python -m unittest discover -s tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workspaces import Base                    # noqa: E402
from test_forge import Forge                        # noqa: E402
from app import db                                  # noqa: E402


class WorkspaceRepository(Base):
    def setUp(self):
        super().setUp()
        self.human = self.register('jac', 'human')
        self.forge = Forge()
        self.addCleanup(self.forge.stop)

    def with_forge(self):
        self.call('PUT', '/api/fleet/secrets/FORGEJO_URL', {'value': self.forge.url}, auth=self.human)
        self.call('PUT', '/api/fleet/secrets/FORGEJO_TOKEN', {'value': 'tok'}, auth=self.human)

    def make(self, slug):
        return self.call('POST', '/api/workspaces', {'slug': slug, 'name': slug, 'purpose': 'x'}, auth=self.human, expected=201)

    def test_without_a_forge_the_workspace_is_made_and_says_why(self):
        out = self.make('game')
        self.assertEqual(out['workspace']['repository'], '')
        self.assertIn('FORGEJO_URL', out['repository_note'])

    def test_with_a_forge_the_repository_is_made_with_the_workspace(self):
        self.with_forge()
        out = self.make('game')
        self.assertEqual(out['workspace']['repository'], 'fleet/game')
        self.assertNotIn('repository_note', out)
        self.assertIn('fleet/game', self.forge.repos)

    def test_the_button_makes_it_later_and_asking_again_changes_nothing(self):
        out = self.make('game')
        self.assertEqual(out['workspace']['repository'], '')
        self.with_forge()
        r = self.call('POST', '/api/workspaces/game/repository', auth=self.human)
        self.assertEqual(r['repository'], 'fleet/game')
        before = len(self.forge.seen)
        self.call('POST', '/api/workspaces/game/repository', auth=self.human)
        self.assertEqual(len(self.forge.seen), before, 'a workspace that has one asks the forge nothing')

    def test_a_forge_that_has_content_there_is_refused_with_the_reason(self):
        self.with_forge()
        self.forge.repos['fleet/game'] = {'owner': 'fleet', 'empty': False}
        out = self.make('game')
        self.assertEqual(out['workspace']['repository'], '')
        self.assertIn('has content', out['repository_note'])
        r = self.call('POST', '/api/workspaces/game/repository', auth=self.human, expected=409)
        self.assertIn('has content', str(r))

    def test_an_ordinary_bot_cannot_have_one_made(self):
        self.make('game')       # made before the forge is set, so it has none
        self.with_forge()
        ada = self.register('ada', 'claude-code', 'game')
        self.call('POST', '/api/workspaces/game/repository', auth=ada, expected=403)


if __name__ == '__main__':
    unittest.main()
