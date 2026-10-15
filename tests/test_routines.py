"""Routines on the board: /api/routines reads admin.routine as the runner does and writes as `routines add` does.
Run with python -m unittest discover -s tests."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import db, main, routines

CHECK = '#!/usr/bin/env bash\necho \'{"work": false}\'\n'


class Routines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        main.PROJECT_ADMIN_KEY = 'k'
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.human = self.register('jac', 'human')
        self.bot = self.register('ada', 'claude-code')
        self.runner = self.register(routines.cli.ME, 'cron')
        self.call('POST', '/api/projects/access', {'key': 'k'})
        self.call('POST', '/api/projects', {'slug': 'admin', 'name': 'Admin', 'purpose': 'Running the fleet'}, expected=201)
        self.call('POST', '/api/projects/admin/groups', {'slug': 'routine', 'name': 'Routines',
                  'purpose': 'Recurring work', 'scope': 'routines'}, expected=201)

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

    def post(self, body, reply_to=None, auth=None, channel=routines.ADMIN, meta=None):
        return self.call('POST', '/api/messages', {'channel': channel, 'body': body, 'reply_to': reply_to,
                                                   'meta': meta or {}}, auth=auth, expected=201)['message']

    def add(self, auth=None, **kw):
        data = {'name': 'triage', 'channel': 'bot_board', 'check': CHECK, 'prompt': 'Triage these.\r\n',
                'every': 60, 'about': 'triage new issues', **kw}
        return self.call('POST', '/api/routines', data, auth=auth, expected=201)

    def make_admin(self, handle='ada'):
        """As a human does with Bot pool controls on the admin project, without a keeper to report the bot."""
        c = db.conn()
        c.execute('INSERT INTO project_pool_bots(project_id, agent_id) SELECT p.id, a.id FROM projects p, agents a'
                  ' WHERE p.slug=? AND a.handle=?', ('admin', handle))
        c.commit()

    def only(self):
        rs = self.call('GET', '/api/routines')['routines']
        self.assertEqual(len(rs), 1)
        return rs[0]

    def test_a_routine_posted_from_the_page_is_what_routines_add_writes(self):
        out = self.add()
        r = self.only()
        self.assertEqual((r['state'], r['problem'], r['thread'], r['code']), ('paused', '', out['thread'], out['code']))
        self.assertEqual(r['prompt'], 'Triage these.')
        defn = {k: r[k] for k in ('name', 'channel', 'check', 'prompt', 'every', 'claim_timeout',
                                  'done_timeout', 'resends', 'about')}
        self.assertEqual(out['message']['body'], routines.cli.body_of(defn))
        self.assertEqual(out['code'], routines.cli.code_of(defn))

    def test_only_a_human_switches_it_and_only_with_the_current_code(self):
        out = self.add()
        self.post(f"enable {out['code']}", out['thread'], auth=self.bot)
        self.assertEqual(self.only()['state'], 'paused')
        self.post(f"enable {out['code']}", out['thread'])
        self.assertEqual(self.only()['state'], 'enabled')
        self.post('pause', out['thread'])
        self.assertEqual(self.only()['state'], 'paused')
        v2 = self.add(every=300, thread=out['thread'])
        self.assertNotEqual(v2['code'], out['code'])
        self.post(f"enable {out['code']}", out['thread'])
        r = self.only()
        self.assertEqual((r['state'], r['every'], r['version']), ('paused', 300, v2['message']['id']))
        self.post(f"enable {v2['code']}", out['thread'])
        self.assertEqual(self.only()['state'], 'enabled')

    def test_bad_definitions_are_refused(self):
        self.call('POST', '/api/routines', {'name': 'x', 'channel': 'bot_board', 'check': '  ', 'prompt': 'p'},
                  expected=422)
        self.call('POST', '/api/routines', {'name': 'x', 'channel': 'bot_board', 'check': CHECK, 'prompt': 'p',
                                            'every': 0}, expected=422)
        lobby = self.post('hello', channel='lobby')
        self.call('POST', '/api/routines', {'name': 'x', 'channel': 'bot_board', 'check': CHECK, 'prompt': 'p',
                                            'thread': lobby['id']}, expected=404)
        self.call('POST', '/api/routines', {'name': 'x', 'channel': 'bot_board', 'check': 'x' * main.MAX_BODY,
                                            'prompt': 'p'}, expected=422)
        self.assertEqual(self.call('GET', '/api/routines')['routines'], [])

    def test_jobs_failures_and_tests_are_reported(self):
        out = self.add()
        self.post(f"enable {out['code']}", out['thread'])
        job = self.post('Triage these. found: #1', channel='bot_board', auth=self.runner,
                        meta={'routine': out['thread'], 'name': 'triage'})
        self.post('Still open', job['id'], channel='bot_board', auth=self.runner,
                  meta={'routine': out['thread'], 'resend': 1})
        self.post('taking this', job['id'], channel='bot_board', auth=self.bot)
        self.post('done: labelled #1', job['id'], channel='bot_board', auth=self.bot, meta={'done': True})
        self.post('test', out['thread'])
        self.post('Test: no work.', out['thread'], auth=self.runner,
                  meta={'routine_test': {'ok': True, 'work': False, 'error': None}})
        self.post('Routine `triage` stopped: the check exited 1', out['thread'], auth=self.runner,
                  meta={'routine_failed': True})
        r = self.only()
        self.assertEqual(r['state'], 'failed')
        self.assertIn('the check exited 1', r['failure'])
        self.assertEqual((r['last_test']['ok'], r['last_test']['work']), (True, False))
        self.assertEqual(len(r['jobs']), 1)
        self.assertEqual({k: r['jobs'][0][k] for k in ('id', 'sends', 'done_by')},
                         {'id': job['id'], 'sends': 2, 'done_by': 'ada'})

    def test_the_admin_bot_answers_a_request_and_enables_it_only_with_a_human_yes(self):
        ask = self.post('Every new issue on bot_board should be triaged')
        early = self.post('sounds right', ask['id'])         # said before the definition: approves nothing
        out = self.add(thread=ask['id'], auth=self.bot)
        r = self.only()
        self.assertEqual((r['thread'], r['state'], r['problem']), (ask['id'], 'paused', ''))
        self.assertNotIn('(new version)', out['message']['body'])
        path = f"/api/routines/{ask['id']}/enable"
        self.call('POST', path, {'approved_by': early['id']}, auth=self.bot, expected=403)   # not an admin bot yet
        self.make_admin()
        self.call('POST', path, {}, auth=self.bot, expected=422)
        self.call('POST', path, {'approved_by': early['id']}, auth=self.bot, expected=422)
        self.post(f"enable {out['code']}", ask['id'], auth=self.bot, meta={'approved_by': early['id']})
        self.assertEqual(self.only()['state'], 'paused', 'a yes from before the definition does not count')
        yes = self.post('yes, go', ask['id'])
        t = self.call('POST', f"/api/routines/{ask['id']}/test", {'approved_by': yes['id']}, auth=self.bot, expected=201)
        self.assertEqual(self.only()['tests'], [t['message']['id']])
        self.call('POST', path, {'approved_by': yes['id']}, auth=self.bot, expected=201)
        self.assertEqual(self.only()['state'], 'enabled')
        v2 = self.add(thread=ask['id'], every=300, auth=self.bot)    # a change needs a new yes
        self.assertIn('(new version)', v2['message']['body'])
        self.call('POST', path, {'approved_by': yes['id']}, auth=self.bot, expected=422)
        self.assertEqual(self.only()['state'], 'paused')
        self.call('POST', f"/api/routines/{ask['id']}/pause", None, auth=self.bot, expected=201)

    def test_delete_stops_it_for_good(self):
        out = self.add()
        self.post(f"enable {out['code']}", out['thread'])
        self.call('DELETE', f"/api/routines/{out['thread']}", auth=self.bot, expected=403)
        self.call('DELETE', f"/api/routines/{out['thread']}")
        self.assertEqual(self.call('GET', '/api/routines')['routines'], [])
        self.post(f"enable {out['code']}", out['thread'])
        self.assertEqual(self.call('GET', '/api/routines')['routines'], [])
        self.call('DELETE', f"/api/routines/{out['thread']}", expected=404)

    def test_a_board_without_the_admin_group_says_how_to_set_it_up(self):
        db.conn().execute('DELETE FROM project_groups'); db.conn().commit()
        r = self.client.post('/api/routines', json={'name': 'x', 'channel': 'bot_board', 'check': CHECK, 'prompt': 'p'},
                             headers=self.human)
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn('group `routine`', r.text)


if __name__ == '__main__':
    unittest.main()
