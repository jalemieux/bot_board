"""#83: the keeper checks credentials before starting bots, holds them while a login is
missing, and keeps fleet.env in step with the central copy. Run with python -m unittest discover -s tests."""
import importlib.machinery
import importlib.util
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from app import db, main


def load_keeper(home, **env):
    """bin/keeper with a scratch home, so it never reads or writes the real ~/.config/bot_board."""
    saved = {k: os.environ.get(k) for k in ('HOME', 'BOT_CONFIG', *env)}
    os.environ.update(HOME=home, BOT_CONFIG=os.path.join(home, 'config'), **env)
    try:
        loader = importlib.machinery.SourceFileLoader('keeper', str(ROOT / 'bin' / 'keeper'))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader('keeper', loader))
        loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    return mod


def fake(bindir, name, script):
    p = os.path.join(bindir, name)
    with open(p, 'w') as f:
        f.write('#!/bin/sh\n' + script + '\n')
    os.chmod(p, 0o755)


class Keeper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.bin = os.path.join(self.home, 'bin'); os.makedirs(self.bin)
        self.central = os.path.join(self.home, 'central'); os.makedirs(self.central)
        os.makedirs(os.path.join(self.home, '.config', 'bot_board'))
        # gh is happy only with the token from fleet.env; claude says whether a login file exists
        fake(self.bin, 'gh', '[ "$GH_TOKEN" = good ]')
        fake(self.bin, 'claude', f'[ -e {self.home}/claude-login ] && echo \'{{"loggedIn": true}}\' '
                                 '|| echo \'{"loggedIn": false}\'')
        self.k = load_keeper(self.home)
        os.environ['KEEPER_FLEET_ENV'] = os.path.join(self.central, 'fleet.env')
        self.path = os.environ['PATH']
        os.environ['PATH'] = self.bin + os.pathsep + self.path

    def tearDown(self):
        os.environ['PATH'] = self.path
        os.environ.pop('KEEPER_FLEET_ENV', None)
        self.tmp.cleanup()

    def creds(self):
        c = self.k.Credentials('box')
        c.refresh({'claude'}, force=True)
        return c

    def want(self, h='tarzan'):
        return {'handle': h, 'harness': 'claude', 'run': True, 'set_by': 'op', 'set_at': '2026-10-15T01:00:00Z',
                'move_to': None, 'fetch': None, 'kill': None}

    def test_no_login_holds_the_bot_and_says_why(self):
        c = self.creds()
        self.assertEqual(c.state['claude'][0], 'missing')
        hands = self.k.Hands('box')
        spawned = []
        hands.spawn = lambda h, cmd: spawned.append(cmd)
        hands.blocked = c.blocked()
        problems = hands.reconcile([self.want()], [])
        self.assertEqual(spawned, [])
        self.assertEqual(problems['tarzan'], 'held: claude not logged in')
        row = next(r for r in c.report(hands.held) if r['name'] == 'claude')
        self.assertEqual((row['state'], row['held']), ('missing', 1))
        # the login comes back: the next round starts it, with no give-up left over
        open(os.path.join(self.home, 'claude-login'), 'w').close()
        hands.blocked = self.creds().blocked()
        self.assertEqual(hands.reconcile([self.want()], []), {})
        self.assertEqual(len(spawned), 1)

    def test_codex_login_does_not_hold_claude_bots(self):
        open(os.path.join(self.home, 'claude-login'), 'w').close()
        c = self.creds()
        self.assertNotIn('codex', c.state)      # no codex bot here: not checked, not reported
        self.assertEqual(c.blocked(), {})

    def test_fleet_env_is_copied_private_and_used(self):
        c = self.creds()
        self.assertEqual(c.state['gh'][0], 'missing')
        src = os.path.join(self.central, 'fleet.env')
        with open(src, 'w') as f:
            f.write('# fleet secrets\nexport GH_TOKEN="good"  # rotated 10-15\n')
        c = self.creds()
        local = os.path.join(self.home, '.config', 'bot_board', 'fleet.env')
        self.assertEqual(stat.S_IMODE(os.stat(local).st_mode), 0o600)
        self.assertEqual(c.env()['GH_TOKEN'], 'good')
        self.assertEqual(c.state['gh'][0], 'ok')
        # rotated to a bad token: one file changes, every box sees it on its next check
        with open(src, 'w') as f:
            f.write('GH_TOKEN=revoked\n')
        c = self.creds()
        self.assertEqual(c.state['gh'][0], 'expired')
        self.assertNotIn('revoked', str(c.report({})))   # the secret never goes into the report

    def test_a_missing_central_copy_keeps_ours_and_says_so(self):
        local = os.path.join(self.home, '.config', 'bot_board', 'fleet.env')
        with open(local, 'w') as f:
            f.write('GH_TOKEN=good\n')
        c = self.creds()
        self.assertEqual(c.state['gh'][0], 'ok')
        self.assertIn('stale', [r['state'] for r in c.report({})])


class Page(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, 'board.db')
        db._local = threading.local()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if hasattr(db._local, 'conn'):
            db._local.conn.close()
        self.tmp.cleanup()

    def test_fleet_shows_credentials(self):
        tok = self.client.post('/api/agents', json={'handle': 'keeper-box', 'kind': 'keeper'}).json()['token']
        r = self.client.put('/api/fleet/hosts/box', headers={'Authorization': 'Bearer ' + tok}, json={
            'bots': [], 'credentials': [
                {'name': 'gh', 'kind': 'secret', 'state': 'ok'},
                {'name': 'claude', 'kind': 'login', 'state': 'missing', 'detail': 'not logged in', 'held': 2}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get('/api/fleet').json()['hosts'][0]['credentials'][1]['held'], 2)
        page = self.client.get('/fleet').text
        self.assertIn('claude missing (held 2 bots): not logged in', page)
        self.assertIn('gh ok', page)


if __name__ == '__main__':
    unittest.main()
