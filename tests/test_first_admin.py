"""With no management key, the first human to sign in is the board admin, and the admin
workspace is created then, so a fresh board always has somewhere for a bot to go (#181).
Run with python -m unittest discover -s tests."""
import unittest

from test_workspaces import Base
from app import db, main, workspaces


class FirstAdmin(Base):
    def before_start(self):
        main.PROJECT_ADMIN_KEY = ''

    def test_first_human_is_admin_and_gets_the_admin_workspace(self):
        self.register('keeper-box', 'keeper')
        self.register('ada', 'claude-code')
        with self.assertRaises(KeyError):
            workspaces.find(workspaces.ADMIN)          # bots and keepers make nobody admin
        jac = self.register('jac', 'human')
        self.assertTrue(self.call('GET', '/api/me', auth=jac)['can_create_workspaces'])
        self.assertEqual(workspaces.find(workspaces.ADMIN)['owner_id'], db.get_agent_by_handle('jac')['id'])
        # a bot can now register into it
        self.register('milo', 'claude-code', workspace=workspaces.ADMIN)
        # the second human is not an admin
        sam = self.register('sam', 'human')
        self.assertFalse(self.call('GET', '/api/me', auth=sam)['can_create_workspaces'])

    def test_a_board_whose_humans_came_before_gets_its_oldest_as_admin_on_start(self):
        self.register('jac', 'human')
        self.register('sam', 'human')
        with db._write_lock, db.conn() as c:            # a board from before: humans, no admin
            c.execute('DELETE FROM board_admins')
        main._startup()
        self.assertTrue(workspaces.is_admin(db.get_agent_by_handle('jac')))
        self.assertFalse(workspaces.is_admin(db.get_agent_by_handle('sam')))


class WithKey(Base):
    def test_a_key_keeps_admin_behind_the_unlock(self):
        jac = self.register('jac', 'human')
        self.assertFalse(self.call('GET', '/api/me', auth=jac)['can_create_workspaces'])
        with self.assertRaises(KeyError):
            workspaces.find(workspaces.ADMIN)


if __name__ == '__main__':
    unittest.main()
