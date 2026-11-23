"""A workspace has one repository (#714, docs/workspace-repository.md). Run with python -m unittest discover -s tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workspaces import Base                    # noqa: E402
from app import db                                  # noqa: E402


class Repository(Base):
    def setUp(self):
        super().setUp()
        self.human = self.register('jac', 'human')
        for slug in ('game', 'other'):
            self.call('POST', '/api/workspaces', {'slug': slug, 'name': slug, 'purpose': 'x'}, auth=self.human, expected=201)
        self.ada = self.register('ada', 'claude-code', 'game')
        self.root = self.register('root', 'claude-code', 'admin')

    def link(self, slug, repo, auth=None, expected=200):
        return self.call('PUT', f'/api/workspaces/{slug}/repository', {'repository': repo}, auth=auth or self.human, expected=expected)

    def get(self, slug):
        return self.call('GET', f'/api/workspaces/{slug}', auth=self.human)['workspace']

    def test_a_workspace_starts_without_one(self):
        w = self.get('game')
        self.assertEqual((w['repository'], w['repository_url']), ('', ''))

    def test_a_human_links_one_and_the_address_is_put_together_on_read(self):
        self.link('game', 'fleet/game')
        self.assertEqual(self.get('game')['repository'], 'fleet/game')
        self.assertEqual(self.get('game')['repository_url'], '', 'no forge address yet')
        self.call('PUT', '/api/fleet/secrets/FORGEJO_URL', {'value': 'http://forge.example:3000/'}, auth=self.human)
        self.assertEqual(self.get('game')['repository_url'], 'http://forge.example:3000/fleet/game')
        row = db.conn().execute("SELECT repository FROM workspaces WHERE slug='game'").fetchone()
        self.assertEqual(row['repository'], 'fleet/game', 'the address is not stored')

    def test_an_admin_bot_may_and_an_ordinary_bot_may_not(self):
        self.link('game', 'fleet/game', auth=self.root)
        self.link('game', 'fleet/other', auth=self.ada, expected=403)
        self.assertEqual(self.get('game')['repository'], 'fleet/game')

    def test_a_repository_belongs_to_one_workspace_ignoring_case(self):
        self.link('game', 'jac/bot_board')
        self.link('other', 'JAC/Bot_Board', expected=409)
        self.link('game', 'JAC/bot_board')                        # the same workspace may restate it
        self.assertEqual(self.get('other')['repository'], '')

    def test_it_can_be_unlinked_and_then_taken_by_another(self):
        self.link('game', 'jac/bot_board'); self.link('game', '')
        self.assertEqual(self.get('game')['repository'], '')
        self.link('other', 'jac/bot_board')

    def test_the_forge_address_must_be_http_or_https_and_an_archived_workspace_is_not_linked(self):
        self.link('game', 'fleet/game')
        self.call('PUT', '/api/fleet/secrets/FORGEJO_URL', {'value': 'javascript:alert(1)'}, auth=self.human)
        self.assertEqual(self.get('game')['repository_url'], '')
        self.call('PATCH', '/api/workspaces/other', {'archived': True, 'archive_confirm': True}, auth=self.human)
        self.link('other', 'fleet/other', expected=409)

    def test_a_name_that_is_not_owner_slash_name_is_refused(self):
        for bad in ('bot_board', 'a/b/c', '../x', 'a/..', '/x', 'a b/c', 'a/b?x=1', 'a/' + 'b' * 101):
            self.link('game', bad, expected=422)
        self.assertEqual(self.get('game')['repository'], '')


if __name__ == '__main__':
    unittest.main()
