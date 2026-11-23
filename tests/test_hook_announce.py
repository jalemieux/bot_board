"""#711: a new issue on a repository a workspace has linked is announced by the webhook, once, in that workspace's
General channel, and its later events find that thread. Run with python -m unittest discover -s tests."""
import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workspaces import Base                    # noqa: E402
from app import db, hooks                           # noqa: E402

SECRET = "forgejo-signs-with-this-0123"
REPO = "jac/game"


def issue_payload(number=7, action="opened", labels=(), repo=REPO, **over):
    base = {"action": action, "number": number, "repository": {"full_name": repo}, "sender": {"login": "jac"},
            "issue": {"number": number, "title": "Add a pause menu", "html_url": f"https://forge/{repo}/issues/{number}",
                      "body": "Players want it.", "labels": [{"name": n} for n in labels]}}
    base.update(over)
    return base


class Announce(Base):
    def setUp(self):
        super().setUp()
        self.human = self.register('jac', 'human')
        self.call('POST', '/api/workspaces', {'slug': 'game', 'name': 'game', 'purpose': 'x'}, auth=self.human, expected=201)
        self.call('PUT', '/api/workspaces/game/repository', {'repository': REPO}, auth=self.human)
        self.call('PUT', '/api/hooks/forgejo', {'secret': SECRET}, auth=self.human)

    def deliver(self, payload, event='issues', delivery='d-1', expected=None):
        raw = json.dumps(payload).encode()
        sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        r = self.client.post('/api/hooks/forgejo', content=raw, headers={
            'Content-Type': 'application/json', 'X-Forgejo-Event': event, 'X-Forgejo-Event-Type': event,
            'X-Forgejo-Delivery': delivery, 'X-Forgejo-Signature': sig})
        if expected:
            self.assertEqual(r.status_code, expected, r.text)
        return r

    def channel(self, slug='game'):
        return self.call('GET', f'/api/messages?channel={slug}&limit=50', auth=self.human)['messages']

    def test_a_new_unlabelled_issue_is_announced_once_in_general_and_remembered(self):
        r = self.deliver(issue_payload(), expected=201)
        tid = r.json()['thread']
        posts = [m for m in self.channel() if 'New issue to take: #7' in m['body']]
        self.assertEqual(len(posts), 1, posts)
        self.assertEqual(posts[0]['id'], tid)
        self.assertIn('https://forge/jac/game/issues/7', posts[0]['body'])
        self.assertIn('POST /repos/jac/game/issues/7/labels', posts[0]['body'])
        self.deliver(issue_payload(), delivery='d-1', expected=200)             # the same delivery again
        self.assertEqual(len([m for m in self.channel() if 'New issue to take' in m['body']]), 1)

    def test_a_later_event_on_the_issue_reaches_the_announcement_thread(self):
        tid = self.deliver(issue_payload(), expected=201).json()['thread']
        r = self.deliver(issue_payload(action='closed'), delivery='d-2', expected=201)
        self.assertEqual(r.json()['thread'], tid)

    def test_a_label_on_the_issue_wins_over_the_remembered_thread(self):
        tid = self.deliver(issue_payload(), expected=201).json()['thread']
        other = self.call('POST', '/api/messages', {'channel': 'game', 'body': 'my own thread'}, auth=self.human, expected=201)['message']['id']
        r = self.deliver(issue_payload(action='closed', labels=[f'thread:{other}']), delivery='d-2', expected=201)
        self.assertEqual(r.json()['thread'], other)
        self.assertNotEqual(other, tid)

    def test_an_issue_that_arrives_with_a_thread_label_is_not_announced(self):
        other = self.call('POST', '/api/messages', {'channel': 'game', 'body': 'a thread'}, auth=self.human, expected=201)['message']['id']
        self.deliver(issue_payload(labels=[f'thread:{other}']), expected=201)
        self.assertFalse([m for m in self.channel() if 'New issue to take' in m['body']])

    def test_an_unlinked_repository_is_ignored_with_a_reason(self):
        r = self.deliver(issue_payload(repo='jac/elsewhere'), expected=202)
        self.assertIn('no workspace has linked jac/elsewhere', r.json()['ignored'])

    def test_only_a_newly_opened_issue_is_announced(self):
        self.deliver(issue_payload(action='closed'), expected=202)
        self.assertFalse([m for m in self.channel() if 'New issue to take' in m['body']])

    def test_a_running_issue_feed_routine_for_the_repository_keeps_the_announcing(self):
        rc = {'type': 'forgejo-issues', 'repo': REPO}
        with db._write_lock, db.conn() as c:
            human = c.execute("SELECT id FROM agents WHERE handle='jac'").fetchone()['id']
            c.execute("INSERT INTO routines (definition, state, defined_at, created_by, updated_by, created_at, updated_at)"
                      " VALUES (?, 'enabled', 0, ?, ?, '', '')", (json.dumps({'recipe': rc}), human, human))
        r = self.deliver(issue_payload(), expected=202)
        self.assertIn('issue-feed routine', r.json()['ignored'])

    def test_a_handle_in_the_title_mentions_nobody(self):
        self.register('ada', 'claude-code', 'admin')
        p = issue_payload()
        p['issue']['title'] = '@ada please do the thing'
        tid = self.deliver(p, expected=201).json()['thread']
        first = self.call('GET', f'/api/messages/{tid}', auth=self.human)
        text = (first.get('message') or first)['body']
        self.assertIn('New issue to take: #7 ada please do the thing', text)
        self.assertNotIn('@ada', text)
        later = issue_payload(action='closed')
        later['issue']['title'] = '@ada please do the thing'
        self.deliver(later, delivery='d-2', expected=201)
        self.assertFalse([m for m in self.channel() if '@ada' in m['body']])

    def test_a_burst_of_issues_is_capped_per_repository(self):
        old, hooks.ANNOUNCE_CAP = hooks.ANNOUNCE_CAP, 2
        self.addCleanup(setattr, hooks, 'ANNOUNCE_CAP', old)
        for n in (1, 2):
            self.deliver(issue_payload(number=n), delivery=f'd{n}', expected=201)
        r = self.deliver(issue_payload(number=3), delivery='d3', expected=202)
        self.assertIn('announced in the last hour', r.json()['ignored'])
        self.assertEqual(len([m for m in self.channel() if 'New issue to take' in m['body']]), 2)

    def test_a_paused_routine_does_not_count(self):
        rc = {'type': 'forgejo-issues', 'repo': REPO}
        with db._write_lock, db.conn() as c:
            human = c.execute("SELECT id FROM agents WHERE handle='jac'").fetchone()['id']
            c.execute("INSERT INTO routines (definition, state, defined_at, created_by, updated_by, created_at, updated_at)"
                      " VALUES (?, 'paused', 0, ?, ?, '', '')", (json.dumps({'recipe': rc}), human, human))
        self.deliver(issue_payload(), expected=201)


if __name__ == '__main__':
    unittest.main()
