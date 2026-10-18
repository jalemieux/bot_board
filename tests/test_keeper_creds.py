"""#83: the keeper checks credentials before starting bots, holds them while a login is
missing, and writes the board's secrets to fleet.env. Run with python -m unittest discover -s tests."""
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
        os.makedirs(os.path.join(self.home, '.config', 'bot_board'))
        # gh is happy only with the token from fleet.env; claude says whether a login file exists
        fake(self.bin, 'gh', '[ "$GH_TOKEN" = good ] && exit 0; '
                             '[ -n "$GH_TOKEN" ] && echo "The token in GH_TOKEN is invalid." >&2 && exit 1; '
                             'echo "You are not logged into any GitHub hosts." >&2; exit 1')
        fake(self.bin, 'claude', f'[ -e {self.home}/claude-login ] && echo \'{{"loggedIn": true}}\' '
                                 '|| echo \'{"loggedIn": false}\'')
        self.k = load_keeper(self.home)
        self.local = os.path.join(self.home, '.config', 'bot_board', 'fleet.env')
        self.board = {'secrets': {}, 'version': 'v0'}     # what GET /api/fleet/secrets/values answers
        self.fetches = 0
        def call(method, path, body, token=None):
            self.fetches += 1
            return (200, self.board) if token == 'keeper-token' else (403, {'detail': 'not a keeper'})
        self.k.call = call
        self.path = os.environ['PATH']
        os.environ['PATH'] = self.bin + os.pathsep + self.path

    def tearDown(self):
        os.environ['PATH'] = self.path
        self.tmp.cleanup()

    def creds(self, c=None, version=None, token='keeper-token'):
        c = c or self.k.Credentials('box')
        if version:
            c.sync(version, token)
        c.refresh({'claude'}, force=True)
        return c

    def secrets(self, version, **values):
        self.board = {'secrets': values, 'version': version}
        return version

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

    def test_board_secrets_are_written_private_and_used(self):
        c = self.creds()
        self.assertEqual(c.state['gh'][0], 'missing')
        c = self.creds(c, self.secrets('v1', GH_TOKEN='good'))
        self.assertEqual(stat.S_IMODE(os.stat(self.local).st_mode), 0o600)
        self.assertTrue(Path(self.local).read_text().startswith(self.k.FROM_BOARD))
        self.assertEqual(c.env()['GH_TOKEN'], 'good')
        self.assertEqual(c.state['gh'][0], 'ok')
        # rotated to a bad token on the board: the next report carries a new version
        c = self.creds(c, self.secrets('v2', GH_TOKEN='revoked'))
        self.assertEqual(c.state['gh'][0], 'expired')
        self.assertNotIn('revoked', str(c.report({})))   # the secret never goes into the report

    def test_fetched_only_when_the_version_changes(self):
        c = self.creds(None, self.secrets('v1', GH_TOKEN='good'))
        self.creds(c, 'v1'); self.creds(c, 'v1')
        self.assertEqual(self.fetches, 1)
        self.creds(c, self.secrets('v2', GH_TOKEN='better'))
        self.assertEqual(self.fetches, 2)

    def test_a_refused_fetch_keeps_ours_and_says_so(self):
        with open(self.local, 'w') as f:
            f.write(self.k.FROM_BOARD + 'GH_TOKEN=good\n')
        c = self.creds(None, 'v1', token='not-a-keeper')
        self.assertEqual(c.state['gh'][0], 'ok')
        self.assertIn('stale', [r['state'] for r in c.report({})])
        self.assertEqual(Path(self.local).read_text(), self.k.FROM_BOARD + 'GH_TOKEN=good\n')

    def test_no_board_secrets_leave_a_hand_kept_file_but_clear_ours(self):
        with open(self.local, 'w') as f:
            f.write('GH_TOKEN=good\n')
        c = self.creds(None, 'v1')                      # the board has none: ours stays
        self.assertEqual(Path(self.local).read_text(), 'GH_TOKEN=good\n')
        c = self.creds(c, self.secrets('v2', GH_TOKEN='good'))
        c = self.creds(c, self.secrets('v3'))           # removed on the board: removed here
        self.assertNotIn('GH_TOKEN', c.env())

    def test_an_unclear_check_holds_nothing(self):
        # an older claude without `auth status`, then one that prints something we cannot read
        for script in ('echo "error: unknown command auth" >&2; exit 2', 'echo "Loading..."'):
            fake(self.bin, 'claude', script)
            c = self.creds()
            self.assertEqual(c.state['claude'][0], 'unknown')
            self.assertEqual(c.blocked(), {})
            self.assertNotIn('claude claude', c.state['claude'][1])

    def test_gh_checks_only_the_board_token_and_required_host(self):
        fake(self.bin, 'gh', '[ "$1 $2 $3 $4" = "api --hostname github.com user" ] && [ "$GH_TOKEN" = good ] && exit 0; '
                             'echo "invalid enterprise keyring login" >&2; exit 1')
        c = self.creds(None, self.secrets('v1', GH_TOKEN='good'))
        self.assertEqual(c.state['gh'], ('ok', ''))
        os.environ['GH_TOKEN'] = 'personal'
        try:
            c = self.creds(c, self.secrets('v2'))
            self.assertEqual(c.state['gh'][0], 'missing')
            self.assertIn('fleet Secrets', c.state['gh'][1])
            self.assertNotIn('personal', str(c.report({})))
        finally:
            os.environ.pop('GH_TOKEN')

    def test_enterprise_token_is_checked_against_its_host_without_echoing_errors(self):
        fake(self.bin, 'gh', '[ "$1 $2 $3 $4" = "api --hostname git.corp.example.com user" ] && '
                             '[ "$GH_ENTERPRISE_TOKEN" = enterprise ] && exit 0; '
                             'echo "401 invalid secret-echo" >&2; exit 1')
        os.environ['GH_HOST'] = 'git.corp.example.com'
        try:
            c = self.creds(None, self.secrets('v1', GH_ENTERPRISE_TOKEN='enterprise'))
            self.assertEqual(c.state['gh'], ('ok', ''))
            c = self.creds(c, self.secrets('v2', GH_ENTERPRISE_TOKEN='bad'))
            self.assertEqual(c.state['gh'][0], 'expired')
            self.assertIn('git.corp.example.com', c.state['gh'][1])
            self.assertNotIn('secret-echo', str(c.report({})))
        finally:
            os.environ.pop('GH_HOST')

    def test_fleet_env_sets_secrets_only_and_the_environment_wins(self):
        with open(self.local, 'w') as f:
            f.write(self.k.FROM_BOARD + 'GH_TOKEN=good\nBOARD=http://other\nPATH=/nowhere\nnot_a_command\nOTHER_KEY=$(touch x)y\n')
        os.environ['OTHER_KEY'] = 'mine'
        try:
            env = self.creds().env()
        finally:
            os.environ.pop('OTHER_KEY')
        self.assertEqual(env['GH_TOKEN'], 'good')
        self.assertEqual(env['OTHER_KEY'], 'mine')
        self.assertEqual(env['PATH'], os.environ['PATH'])
        self.assertNotEqual(env.get('BOARD'), 'http://other')


class BotReadsFleetEnv(unittest.TestCase):
    def test_a_bad_line_does_not_stop_bin_bot(self):
        import subprocess
        with tempfile.TemporaryDirectory() as home:
            cfg = os.path.join(home, '.config', 'bot_board'); os.makedirs(cfg)
            with open(os.path.join(cfg, 'fleet.env'), 'w') as f:
                f.write(f'not_a_command_xyz\nGH_TOKEN=abc def\nX_TOKEN=$(touch {home}/ran)x\n')
            r = subprocess.run([str(ROOT / 'bin' / 'bot'), 'ps'], capture_output=True, text=True, timeout=60,
                               env={**os.environ, 'HOME': home, 'BOT_CONFIG': os.path.join(cfg, 'config')})
            self.assertNotIn('command not found', r.stderr)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(os.path.exists(os.path.join(home, 'ran')))


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


    def test_secrets_are_set_by_admins_and_read_by_keepers_only(self):
        main.PROJECT_ADMIN_KEY = 'k'
        def agent(handle, kind):
            return {'Authorization': 'Bearer ' + self.client.post('/api/agents', json={'handle': handle, 'kind': kind}).json()['token']}
        admin, human, bot, keeper, idle = (agent('op', 'human'), agent('other', 'human'), agent('ada', 'claude-code'),
                                            agent('keeper-box', 'keeper'), agent('keeper-x', 'keeper'))
        self.client.post('/api/projects/access', headers=admin, json={'key': 'k'})
        report = lambda: self.client.put('/api/fleet/hosts/box', headers=keeper, json={'bots': []}).json()['secrets']
        v0 = report()
        self.assertEqual(self.client.put('/api/fleet/secrets/GH_TOKEN', headers=human, json={'value': 'x'}).status_code, 403)
        self.assertEqual(self.client.put('/api/fleet/secrets/PATH', headers=admin, json={'value': 'x'}).status_code, 422)
        self.assertEqual(self.client.put('/api/fleet/secrets/GH_TOKEN', headers=admin, json={'value': 'a\nb'}).status_code, 422)
        r = self.client.put('/api/fleet/secrets/GH_TOKEN', headers=admin, json={'value': 'ghp_secret1234'})
        self.assertEqual(r.status_code, 200, r.text)
        listed = self.client.get('/api/fleet/secrets', headers=admin).json()['secrets']
        self.assertEqual([(x['name'], x['ends'], x['set_by']) for x in listed], [('GH_TOKEN', '1234', 'op')])
        self.assertNotIn('ghp_secret1234', self.client.get('/api/fleet/secrets', headers=admin).text)
        self.assertNotEqual(report(), v0)
        # the values: the keeper of a box, and nobody else
        self.assertEqual(self.client.get('/api/fleet/secrets/values', headers=keeper).json()['secrets'],
                         {'GH_TOKEN': 'ghp_secret1234'})
        for who in (admin, bot, idle):
            self.assertEqual(self.client.get('/api/fleet/secrets/values', headers=who).status_code, 403)
        self.assertNotIn('ghp_secret1234', self.client.get('/api/fleet').text + self.client.get('/fleet').text)
        self.assertEqual(self.client.delete('/api/fleet/secrets/GH_TOKEN', headers=admin).status_code, 200)
        self.assertEqual(report(), v0)


class BotReloadsFleetEnv(unittest.TestCase):
    def test_rotated_and_removed_secrets_reach_the_next_run(self):
        import subprocess
        src = (ROOT / 'bin' / 'bot').read_text()
        fn = src[src.index('_OWN_SECRETS='):src.index('\nload_fleet_env\n')]
        with tempfile.TemporaryDirectory() as home:
            env_file = os.path.join(home, '.config', 'bot_board', 'fleet.env'); os.makedirs(os.path.dirname(env_file))
            script = fn + f'''
w() {{ printf '%s\\n%s\\n' '# written by bin/keeper from the board' "$1" > {env_file}; }}
w GH_TOKEN=one; load_fleet_env; echo "gh=$GH_TOKEN own=$MY_KEY"
w GH_TOKEN=two; load_fleet_env; echo "gh=$GH_TOKEN"
w MY_KEY=theirs; load_fleet_env; echo "gh=${{GH_TOKEN:-gone}} own=$MY_KEY"
'''
            r = subprocess.run(['bash', '-euo', 'pipefail', '-c', script], capture_output=True, text=True,
                               env={'PATH': os.environ['PATH'], 'HOME': home, 'MY_KEY': 'mine'})
            self.assertEqual(r.stdout.split('\n')[:3], ['gh=one own=mine', 'gh=two', 'gh=gone own=mine'], r.stderr)

if __name__ == '__main__':
    unittest.main()
