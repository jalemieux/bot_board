"""#809: a workspace that gets a repository gets the routine that offers its issues, enabled, once, Forgejo only.
Run with python -m unittest discover -s tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workspaces import Base                    # noqa: E402
from app import db, forge, routines                 # noqa: E402


class IssueRoutine(Base):
    def setUp(self):
        super().setUp()
        self.human = self.register('jac', 'human')
        self.call('POST', '/api/workspaces', {'slug': 'game', 'name': 'game', 'purpose': 'x'}, auth=self.human, expected=201)
        self.call('POST', '/api/workspaces', {'slug': 'other', 'name': 'other', 'purpose': 'x'}, auth=self.human, expected=201)

    def forge_address(self):
        self.call('PUT', '/api/fleet/secrets/FORGEJO_URL', {'value': 'http://forge.example:3000'}, auth=self.human)

    def link(self, slug, repo):
        return self.call('PUT', f'/api/workspaces/{slug}/repository', {'repository': repo}, auth=self.human)['workspace']

    def rows(self):
        return db.conn().execute("SELECT * FROM routines WHERE state<>'deleted' AND definition LIKE '%forgejo-issues%'").fetchall()

    def test_linking_a_repository_makes_an_enabled_routine_once(self):
        self.forge_address()
        w = self.link('game', 'jac/game')
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['state'], rows[0]['resume']), ('enabled', 0))
        self.assertEqual(w['issue_routine'], rows[0]['id'])
        rc = routines.get(rows[0]['id'])
        self.assertEqual((rc['recipe']['repo'], rc['recipe']['workspace'], rc['channel']), ('jac/game', 'game', 'game'))
        self.assertFalse(rc.get('waiting'), 'approved: the board wrote the check from the recipe')
        self.link('game', 'jac/game')                                  # again: still one
        self.assertEqual(len(self.rows()), 1)

    def test_no_forge_address_no_routine(self):
        w = self.link('game', 'jac/game')
        self.assertEqual(self.rows(), [])
        self.assertIsNone(w['issue_routine'])

    def test_a_routine_that_covers_the_repository_is_not_doubled_and_a_paused_one_counts(self):
        self.forge_address()
        self.link('game', 'jac/game')
        rid = self.rows()[0]['id']
        self.call('POST', f'/api/routines/{rid}/pause', auth=self.human)
        self.link('game', '')
        self.link('game', 'jac/game')
        self.assertEqual(len(self.rows()), 1, 'the paused routine still covers it')

    def test_creating_the_repository_makes_it_too(self):
        self.forge_address()
        self.call('PUT', '/api/fleet/secrets/FORGEJO_TOKEN', {'value': 'tok-xxxxxxxxxxxxxxxx'}, auth=self.human)
        real = forge.make_repository
        forge.make_repository = lambda slug, timeout=None: f'fleet/{slug}'
        self.addCleanup(setattr, forge, 'make_repository', real)
        self.call('POST', '/api/workspaces/other/repository', auth=self.human)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(routines.get(rows[0]['id'])['recipe']['repo'], 'fleet/other')

    def test_unlinking_leaves_the_routine_for_a_person_to_decide(self):
        self.forge_address()
        self.link('game', 'jac/game'); self.link('game', '')
        self.assertEqual(len(self.rows()), 1)


if __name__ == '__main__':
    unittest.main()
