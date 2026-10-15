"""Routines on the board: stored rows, changed only through /api/routines, by the right people.
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
        self.stranger = self.register('visitor', 'human')
        self.bot = self.register('ada', 'claude-code')
        self.runner = self.register(routines.cli.ME, 'cron')
        self.call('POST', '/api/projects/access', {'key': 'k'})
        self.assertEqual(self.call('GET', '/api/projects/admin')['project']['readiness'], 'ready')   # comes with the first admin (#103)

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

    def post(self, body, reply_to=None, auth=None, channel='admin', meta=None):
        return self.call('POST', '/api/messages', {'channel': channel, 'body': body, 'reply_to': reply_to,
                                                   'meta': meta or {}}, auth=auth, expected=201)['message']

    def add(self, auth=None, expected=201, **kw):
        data = {'name': 'triage', 'channel': 'bot_board', 'check': CHECK, 'prompt': 'Triage these.\r\n',
                'every': 60, 'about': 'triage new issues', **kw}
        return self.call('POST', '/api/routines', data, auth=auth, expected=expected)

    def make_admin(self, handle='ada'):
        """As a human does with Bot pool controls on the admin project, without a keeper to report the bot."""
        c = db.conn()
        c.execute('INSERT INTO project_pool_bots(project_id, agent_id) SELECT p.id, a.id FROM projects p, agents a'
                  ' WHERE p.slug=? AND a.handle=?', ('admin', handle))
        c.commit()

    def test_the_admin_project_comes_seeded_and_asks_for_its_bot(self):
        # #103: created with the first admin, context published, and flagged until a bot is dedicated to it
        p = self.call('GET', '/api/projects/admin')['project']
        self.assertEqual(p['context']['revision'], 1)
        self.assertIn('Nothing runs without a human yes', p['context']['body'])
        self.assertTrue(any('has no admin bot' in a['text'] for a in self.call('GET', '/api/status')['attention']))
        self.assertFalse(routines.ensure_admin_project())     # an existing one is left as it is
        self.call('PATCH', '/api/projects/admin', {'name': 'Ops'})
        self.call('POST', '/api/projects/access', {'key': 'k'}, auth=self.stranger)
        self.assertEqual(self.call('GET', '/api/projects/admin')['project']['name'], 'Ops')

    def test_a_board_with_an_admin_but_no_admin_project_gets_one_at_startup(self):
        c = db.conn()
        for t in ('project_pool_bots', 'project_pools', 'context_revisions', 'project_audit'):
            c.execute(f'DELETE FROM {t}')
        c.execute("DELETE FROM projects WHERE slug='admin'"); c.commit()
        self.call('POST', '/api/projects/access', {'key': 'k'}, auth=self.stranger)   # a later admin
        c.execute("DELETE FROM projects WHERE slug='admin'"); c.execute('DELETE FROM context_revisions'); c.commit()
        main._startup()
        p = self.call('GET', '/api/projects/admin')['project']
        self.assertEqual(p['context']['revision'], 1)
        owner = c.execute("SELECT a.handle FROM projects p JOIN agents a ON a.id=p.owner_id WHERE p.slug='admin'").fetchone()
        self.assertEqual(owner[0], 'jac')     # the first admin, not the latest

    def test_a_slug_that_cannot_be_adopted_does_not_break_start_or_unlock(self):
        c = db.conn()
        c.execute("DELETE FROM projects WHERE slug='admin'"); c.execute('DELETE FROM context_revisions')
        c.execute("UPDATE boards SET kind='conversation' WHERE slug='admin'"); c.commit()
        main._startup()
        self.call('POST', '/api/projects/access', {'key': 'k'}, auth=self.stranger)
        self.call('GET', '/api/projects/admin', expected=404)

    def test_a_stored_routine_is_paused_and_posts_nothing(self):
        before = db.cursor()
        r = self.add()
        self.assertEqual((r['state'], r['version'], r['problem'], r['prompt'], r['every']),
                         ('paused', 1, '', 'Triage these.', 60))
        self.assertEqual(self.call('GET', f"/api/routines/{r['id']}")['name'], 'triage')
        self.assertEqual([x['id'] for x in self.call('GET', '/api/routines')['routines']], [r['id']])
        self.assertEqual(db.cursor(), before, 'storing a routine posts no message')

    def test_only_admin_managers_and_admin_bots_change_routines(self):
        self.add(auth=self.bot, expected=403)
        self.add(auth=self.stranger, expected=403)     # kind human is not authority
        r = self.add()
        self.call('POST', f"/api/routines/{r['id']}/pause", auth=self.stranger, expected=403)
        self.call('DELETE', f"/api/routines/{r['id']}", auth=self.bot, expected=403)
        self.make_admin()
        self.call('POST', f"/api/routines/{r['id']}/pause", auth=self.bot)
        self.call('POST', f"/api/routines/{r['id']}/enable")          # a manager needs no cited yes
        self.assertEqual(self.call('GET', f"/api/routines/{r['id']}")['state'], 'enabled')

    def test_an_admin_bot_enables_only_on_a_managers_yes_to_the_current_version(self):
        self.make_admin()
        ask = self.post('Every new issue on bot_board should be triaged')
        early = self.post('sounds right', ask['id'])          # before the definition: approves nothing
        r = self.add(auth=self.bot, thread=ask['id'])
        self.assertEqual(r['thread'], ask['id'])
        enable = f"/api/routines/{r['id']}/enable"
        self.call('POST', enable, {}, auth=self.bot, expected=422)
        self.call('POST', enable, {'approved_by': early['id']}, auth=self.bot, expected=422)
        for who in (self.bot, self.stranger):                    # a yes must come from a manager
            other = self.post('yes', ask['id'], auth=who)
            self.call('POST', enable, {'approved_by': other['id']}, auth=self.bot, expected=422)
        elsewhere = self.post('yes', channel='lobby')
        self.call('POST', enable, {'approved_by': elsewhere['id']}, auth=self.bot, expected=422)
        yes = self.post('yes, go', ask['id'])
        tested = self.call('POST', f"/api/routines/{r['id']}/test", {'approved_by': yes['id']}, auth=self.bot)
        self.assertTrue(tested['test_pending'])
        on = self.call('POST', enable, {'approved_by': yes['id']}, auth=self.bot)
        self.assertEqual((on['state'], on['approved_by']), ('enabled', yes['id']))
        v2 = self.call('PATCH', f"/api/routines/{r['id']}", {'every': 300}, auth=self.bot)
        self.assertEqual((v2['state'], v2['version'], v2['every'], v2['check']), ('paused', 2, 300, CHECK))
        self.call('POST', enable, {'approved_by': yes['id']}, auth=self.bot, expected=422)   # a yes approves one version
        yes2 = self.post('ok', ask['id'])
        self.assertEqual(self.call('POST', enable, {'approved_by': yes2['id']}, auth=self.bot)['state'], 'enabled')

    def test_delete_stops_it_for_good(self):
        r = self.add()
        self.call('DELETE', f"/api/routines/{r['id']}")
        self.assertEqual(self.call('GET', '/api/routines')['routines'], [])
        self.call('POST', f"/api/routines/{r['id']}/enable", expected=404)
        self.call('DELETE', f"/api/routines/{r['id']}", expected=404)

    def test_only_the_runner_reports_and_a_failure_only_fails_the_version_it_ran(self):
        r = self.add()
        report = f"/api/routines/{r['id']}/report"
        self.call('POST', report, {'failed': 'x'}, expected=403)
        self.call('POST', f"/api/routines/{r['id']}/test")
        got = self.call('POST', report, {'test': {'for': 1, 'version': 1, 'ok': True, 'work': False}}, auth=self.runner)
        self.assertFalse(got['test_pending'])
        self.assertEqual(got['last_test']['ok'], True)
        self.call('POST', f"/api/routines/{r['id']}/enable")
        self.call('PATCH', f"/api/routines/{r['id']}", {'every': 90})
        self.call('POST', f"/api/routines/{r['id']}/enable")
        self.assertEqual(self.call('POST', report, {'failed': 'old', 'version': 1}, auth=self.runner)['state'], 'enabled')
        got = self.call('POST', report, {'failed': 'the check exited 1', 'version': 2}, auth=self.runner)
        self.assertEqual((got['state'], got['failure']), ('failed', 'the check exited 1'))

    def test_bad_definitions_are_refused(self):
        self.add(check='  ', expected=422)
        self.add(every=0, expected=422)
        self.add(name=None, expected=422)
        self.add(check='x' * 10, recipe={'type': 'github-issues', 'repo': 'o/r', 'project': 'nope'}, expected=404)
        self.add(thread=999999, expected=404)
        self.assertEqual(self.call('GET', '/api/routines')['routines'], [])

    def test_jobs_are_reported_from_the_messages_the_runner_fired(self):
        r = self.add()
        job = self.post('Triage these. found: #1', channel='bot_board', auth=self.runner,
                        meta={'routine': r['id'], 'name': 'triage'})
        self.post('Still open', job['id'], channel='bot_board', auth=self.runner, meta={'routine': r['id'], 'resend': 1})
        self.post('done: labelled #1', job['id'], channel='bot_board', auth=self.bot, meta={'done': True})
        (j,) = self.call('GET', '/api/routines')['routines'][0]['jobs']
        self.assertEqual({k: j[k] for k in ('id', 'sends', 'done_by')}, {'id': job['id'], 'sends': 2, 'done_by': 'ada'})


if __name__ == '__main__':
    unittest.main()
