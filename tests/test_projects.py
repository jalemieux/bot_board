"""Project management and context checks. Run with python -m unittest discover -s tests."""
import concurrent.futures
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main


class Projects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        main.PROJECT_ADMIN_KEY = 'test-management-key'
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register('owner', 'human')
        self.bot = self.register('worker', 'codex')
        self.other = self.register('visitor', 'human')
        self.call('POST', '/api/projects/access', {'key': main.PROJECT_ADMIN_KEY}, expected=200)
        self.call('POST', '/api/projects', {'slug': 'game', 'name': 'Game', 'purpose': 'A game',
                  'repositories': [{'name': 'game', 'url': 'https://github.com/example/game'}]}, expected=201)
        self.call('POST', '/api/projects/game/groups', {'slug': 'art', 'name': 'Art',
                  'purpose': 'Consistent art', 'scope': 'Assets and style'}, expected=201)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def register(self, handle, kind):
        r = self.client.post('/api/agents', json={'handle': handle, 'kind': kind})
        self.assertEqual(r.status_code, 201, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def call(self, method, path, data=None, auth=None, expected=200):
        r = self.client.request(method, path, json=data, headers=auth or self.human)
        self.assertEqual(r.status_code, expected, r.text)
        return r.json()

    def context(self, body='Approved palette', base=0, mode='draft', **kw):
        return {'base_revision': base, 'body': body, 'sources': ['https://github.com/example/game/pull/1'],
                'reason': 'Preserve prior learning', 'mode': mode, **kw}

    def test_human_label_is_not_management_authority(self):
        self.call('POST', '/api/projects/access', {'key': 'wrong'}, auth=self.other, expected=403)
        self.call('POST', '/api/projects/access', {'key': main.PROJECT_ADMIN_KEY}, auth=self.bot, expected=403)
        self.call('POST', '/api/projects/game/groups', {'slug': 'lore', 'name': 'Lore', 'purpose': 'Stories'},
                  auth=self.other, expected=403)
        self.call('PATCH', '/api/projects/game', {'operators': ['visitor']})
        self.call('POST', '/api/projects/game/groups', {'slug': 'lore', 'name': 'Lore', 'purpose': 'Stories'},
                  auth=self.other, expected=201)

    def test_seed_draft_approval_and_channel_route(self):
        seed = self.call('POST', '/api/projects/game/groups/art/seed',
                         {'sources': ['old/docs/art.md'], 'scope': 'Earlier art PRs'})['message']
        self.assertEqual(seed['meta']['kind'], 'context_seed')
        repeated = self.call('POST', '/api/projects/game/groups/art/seed',
                             {'sources': ['old/docs/art.md'], 'scope': 'Earlier art PRs'})
        self.assertEqual(repeated['message']['id'], seed['id'])
        path = '/api/projects/game/groups/art/context'
        self.call('POST', path, self.context(mode='publish'), auth=self.bot, expected=403)
        draft = self.call('POST', path, self.context(), auth=self.bot)
        self.assertEqual(draft['readiness'], 'draft')
        self.assertEqual(draft['context']['revision'], 0)
        self.call('POST', path + '/approve', {'id': draft['id'], 'base_revision': 0}, auth=self.bot, expected=403)
        approved = self.call('POST', path + '/approve', {'id': draft['id'], 'base_revision': 0})
        self.assertEqual(approved['context']['revision'], 1)
        self.assertEqual(approved['context']['author'], 'worker')
        route = self.call('GET', '/api/channels/game.art/project')
        self.assertEqual(route['project']['slug'], 'game')
        self.assertEqual(route['group']['context']['body'], 'Approved palette')
        self.assertEqual(self.call('GET', '/api/channels/lobby/project'), {'project': None, 'group': None})

    def test_stale_context_cannot_overwrite_or_approve(self):
        path = '/api/projects/game/groups/art/context'
        draft = self.call('POST', path, self.context(), auth=self.bot)
        self.call('POST', path, self.context('New style', mode='publish'))
        self.call('POST', path, self.context('Stale write', mode='publish'), expected=409)
        self.call('POST', path + '/approve', {'id': draft['id'], 'base_revision': 1}, expected=409)
        self.assertEqual(self.call('GET', '/api/projects/game/groups/art')['group']['context']['body'], 'New style')
        self.call('POST', path, self.context('Routine notes', base=1, mode='publish'), auth=self.bot, expected=403)
        self.call('POST', path, {'base_revision': 1, 'body': '', 'sources': [], 'reason': 'Blank', 'mode': 'empty'},
                  auth=self.bot, expected=403)
        self.call('POST', path, self.context('Bypass', base=1, mode='publish', decision_change=False), auth=self.bot, expected=422)
        self.call('POST', path, self.context('Change a decision', base=1), auth=self.bot)

    def test_reserved_events_and_pending_draft_cap(self):
        for kind in ['context_update', 'context_seed']:
            self.call('POST', '/api/messages', {'channel': 'game.art', 'body': 'Spoof', 'meta': {'kind': kind}},
                      auth=self.bot, expected=403)
        seed = self.call('POST', '/api/projects/game/groups/art/seed', {'scope': 'Old art work'})['message']
        reply = self.call('POST', '/api/messages', {'channel': 'help', 'reply_to': seed['id'],
                          'body': 'Taking this'}, auth=self.bot, expected=201)
        self.assertEqual(reply['message']['meta']['kind'], 'context_seed')
        for i in range(10):
            self.call('POST', '/api/projects/game/groups/art/context', self.context(str(i)), auth=self.bot)
        self.call('POST', '/api/projects/game/groups/art/context', self.context(), auth=self.bot, expected=409)
        self.call('POST', '/api/messages', {'channel': 'game.sound', 'body': 'Squat'}, auth=self.bot, expected=403)
        self.call('POST', '/api/channels', {'slug': 'game.sound'}, auth=self.bot, expected=403)

    def test_concurrent_context_publication_has_one_winner(self):
        path = '/api/projects/game/groups/art/context'
        def publish(i):
            return self.client.post(path, json=self.context(str(i), mode='publish'), headers=self.human).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(publish, [1, 2]))
        self.assertEqual(sorted(statuses), [200, 409])

    def test_explicit_empty_seed_and_archive_preserve_history(self):
        path = '/api/projects/game/groups/art'
        self.call('POST', path + '/context', {'base_revision': 0, 'body': '', 'sources': [],
                  'reason': 'No prior work', 'mode': 'empty'})
        self.call('PATCH', path, {'archived': True}, expected=422)
        self.call('PATCH', path, {'archived': True, 'archive_confirm': True})
        self.assertTrue(self.call('GET', path)['group']['archived'])
        self.assertEqual(self.call('GET', path)['group']['context']['revision'], 1)
        self.call('POST', '/api/messages', {'channel': 'game.art', 'body': 'New work'}, expected=403)
        self.call('POST', path + '/context', self.context(base=1), expected=409)
        self.call('GET', path + '/context/revisions')

    def test_adopt_existing_channel_without_rewriting_messages(self):
        old = self.call('POST', '/api/messages', {'channel': 'older-game', 'body': 'Earlier art lesson'}, expected=201)
        create = {'slug': 'older-game', 'name': 'Existing game', 'purpose': 'Keep learning'}
        self.call('POST', '/api/projects', create, expected=409)
        self.call('POST', '/api/projects', {**create, 'adopt_channel': True}, expected=201)
        message = self.call('GET', '/api/messages/' + str(old['message']['id']))['message']
        self.assertEqual(message['body'], 'Earlier art lesson')
        self.call('PATCH', '/api/projects/older-game', {'archived': True, 'archive_confirm': True})
        self.call('POST', '/api/messages', {'channel': 'older-game', 'body': 'New'}, expected=403)

    def test_context_bounds_revision_scope_and_list_paging(self):
        path = '/api/projects/game/groups/art/context'
        self.call('POST', path, self.context('x' * 16001), expected=422)
        self.call('POST', path, self.context(mode='publish', sources=[]), expected=422)
        draft = self.call('POST', path, self.context(), auth=self.bot)
        self.call('GET', '/api/projects/game/context/revisions/' + str(draft['id']), expected=404)
        self.call('POST', path + '/revisions/' + str(draft['id']) + '/discard')
        self.assertEqual(self.call('GET', '/api/projects/game/groups/art')['group']['readiness'], 'unseeded')
        self.assertFalse(self.call('GET', '/api/projects')['has_more'])
        page = self.call('GET', '/api/projects?limit=1')
        self.assertEqual(len(page['projects']), 1)

    def test_schema_upgrade_and_legacy_posting(self):
        db.init()
        self.call('POST', '/api/messages', {'channel': 'legacy', 'body': 'Still works'}, auth=self.bot, expected=201)
        self.assertEqual(self.call('GET', '/api/projects/game')['project']['slug'], 'game')
        self.call('PATCH', '/api/projects/game', {'allocation': {'mode': 'project', 'target': 3}}, expected=422)
        self.assertIn('/projects', self.client.get('/').text)
        self.assertEqual(self.client.get('/projects/game').status_code, 200)

    def test_repository_and_metadata_validation(self):
        self.call('PATCH', '/api/projects/game', {'repositories': [{'name': 'bad', 'url': 'file:///tmp/private'}]}, expected=422)
        self.call('PATCH', '/api/projects/game', {'name': None}, expected=422)
        self.call('PATCH', '/api/projects/game', {'owner_id': 99}, expected=422)

    def report_pool(self, bots=None):
        self.call('PUT', '/api/fleet/hosts/test-box', {'bots': bots if bots is not None else [
            {'handle': 'worker', 'harness': 'codex', 'status': 'waiting', 'pool': True}]})

    def test_project_pool_permissions_conflicts_and_archive_release(self):
        self.report_pool()
        pool = self.call('GET', '/api/projects/game/bots')
        self.assertFalse(pool['configured'])
        self.assertTrue(pool['candidates'][0]['available'])
        data = {'configured': True, 'bots': ['worker'], 'base_revision': 0}
        self.call('PUT', '/api/projects/game/bots', data, auth=self.bot, expected=403)
        self.call('PUT', '/api/projects/game/bots', data, auth=self.other, expected=403)
        saved = self.call('PUT', '/api/projects/game/bots', data)
        self.assertEqual(saved['bots'], ['worker'])
        self.assertEqual(self.call('GET', '/api/me/project-pool', auth=self.bot)['project'], 'game')
        self.assertTrue(self.call('GET', '/api/channels/game.art/project')['project']['bot_pool']['configured'])
        self.call('PUT', '/api/projects/game/bots', data, expected=409)
        self.call('POST', '/api/projects', {'slug': 'other-game', 'name': 'Other', 'purpose': 'Elsewhere'}, expected=201)
        self.call('PUT', '/api/projects/other-game/bots', data, expected=409)
        self.call('PATCH', '/api/projects/game', {'archived': True, 'archive_confirm': True})
        self.assertIsNone(self.call('GET', '/api/me/project-pool', auth=self.bot)['project'])
        self.call('PUT', '/api/projects/other-game/bots', data)

    def test_offline_pool_members_are_retained_and_can_be_released(self):
        self.report_pool()
        path = '/api/projects/game/bots'
        self.call('PUT', path, {'configured': True, 'bots': ['worker'], 'base_revision': 0})
        self.report_pool([])
        offline = self.call('GET', path)
        self.assertEqual(offline['bots'], ['worker'])
        self.assertFalse(offline['candidates'][0]['available'])
        self.call('PUT', path, {'configured': True, 'bots': ['worker'], 'base_revision': 1})
        self.call('PUT', path, {'configured': False, 'bots': [], 'base_revision': 2})
        self.assertIsNone(self.call('GET', '/api/me/project-pool', auth=self.bot)['project'])

    def test_pool_rejects_non_pool_bots_and_accepts_empty_dedication(self):
        self.report_pool([{'handle': 'worker', 'harness': 'codex', 'status': 'waiting', 'pool': False}])
        path = '/api/projects/game/bots'
        self.call('PUT', path, {'configured': True, 'bots': ['worker'], 'base_revision': 0}, expected=422)
        self.call('PUT', path, {'configured': False, 'bots': ['worker'], 'base_revision': 0}, expected=422)
        result = self.call('PUT', path, {'configured': True, 'bots': [], 'base_revision': 0})
        self.assertTrue(result['configured'])
        self.assertEqual(result['bots'], [])

    def test_concurrent_projects_cannot_take_the_same_bot(self):
        self.report_pool()
        self.call('POST', '/api/projects', {'slug': 'other-game', 'name': 'Other', 'purpose': 'Elsewhere'}, expected=201)
        def assign(slug):
            return self.client.put('/api/projects/'+slug+'/bots', headers=self.human,
                json={'configured': True, 'bots': ['worker'], 'base_revision': 0}).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(assign, ['game', 'other-game'])), [200, 409])

    def test_claimed_bootstrap_auto_publishes_once_with_references(self):
        path = '/api/projects/game/groups/art'
        seed = self.call('POST', path+'/seed', {'sources': ['docs/art.md'], 'scope': 'Earlier art work'})['message']
        self.assertEqual(seed['channel'], 'help')
        route = self.call('GET', '/api/threads/'+str(seed['id'])+'/seed')
        self.assertEqual(route['group']['slug'], 'art')
        self.assertFalse(route['completed'])
        self.call('POST', path+'/seed/claim', {'thread_id': seed['id']}, auth=self.bot)
        self.call('POST', path+'/seed/claim', {'thread_id': seed['id']}, auth=self.other, expected=409)
        self.call('POST', path+'/context', self.context(sources=[]), auth=self.bot, expected=422)
        self.call('POST', path+'/context', self.context(sources=['Read the board']), auth=self.bot, expected=422)
        result = self.call('POST', path+'/context', self.context(), auth=self.bot)
        self.assertEqual(result['readiness'], 'ready')
        self.assertEqual(result['context']['revision'], 1)
        self.assertEqual(result['context']['sources'], self.context()['sources'])
        self.assertIsNone(result['context']['approved_by'])
        self.call('POST', path+'/context', self.context(base=1, mode='publish'), auth=self.bot, expected=403)
        self.call('POST', path+'/seed/claim', {'thread_id': seed['id']}, auth=self.bot, expected=409)
        self.assertTrue(self.call('GET', '/api/threads/'+str(seed['id'])+'/seed')['completed'])
        self.assertEqual(self.call('GET', path)['group']['seed_claimant'], 'worker')

    def test_seed_claim_is_atomic_and_can_be_released(self):
        path = '/api/projects/game/groups/art/seed'
        seed = self.call('POST', path, {'scope': 'Earlier game work'})['message']
        def claim(auth):
            return self.client.post(path+'/claim', json={'thread_id': seed['id']}, headers=auth).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(claim, [self.bot, self.other])), [200, 409])
        self.call('DELETE', path+'/claim')  # project operator can free abandoned work
        self.call('POST', path+'/claim', {'thread_id': seed['id']}, auth=self.bot)
        self.call('DELETE', path+'/claim', auth=self.other, expected=403)
        self.call('DELETE', path+'/claim', auth=self.bot)
        self.call('POST', path+'/claim', {'thread_id': seed['id']+1000}, auth=self.bot, expected=409)

    def test_untouched_legacy_seed_moves_to_help_once(self):
        from app import projects
        path = '/api/projects/game/groups/art'
        root = self.call('POST', path+'/seed', {'scope': 'Earlier art'})['message']
        _, group = projects.get_scope('game', 'art')
        with db._write_lock, db.conn() as c:
            c.execute('UPDATE messages SET board_id=? WHERE id=?', (group['board_id'], root['id']))
        db.init()
        thread = self.call('GET', '/api/threads/'+str(root['id']))['messages']
        self.assertEqual(len(thread), 2)
        self.assertTrue(all(m['channel']=='help' for m in thread))
        db.init()
        self.assertEqual(len(self.call('GET', '/api/threads/'+str(root['id']))['messages']), 2)


if __name__ == '__main__':
    unittest.main()
