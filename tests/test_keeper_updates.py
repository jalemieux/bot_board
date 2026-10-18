"""Managed upgrades use real local git repositories; no board or service is modified."""
import importlib.util
import importlib.machinery
import plistlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
spec = importlib.util.spec_from_file_location('keeper_update', ROOT / 'bin/keeper_update.py')
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

KEEPER = '''#!/usr/bin/env python3
import os, time, subprocess, sys
from pathlib import Path
from keeper_update import Updates
selfpath = Path(__file__).resolve()
root = Path(os.environ['KEEPER_MANAGED_ROOT'])
u = Updates(root)
u.ready(Path(__file__).resolve().parent.parent.name)
pidfile = os.environ.get('TEST_WORKER_PID_FILE')
if pidfile and not Path(pidfile).exists():
    worker = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=True)
    Path(pidfile).write_text(str(worker.pid))
while True:
    if selfpath != (root / 'src/bin/keeper').resolve():
        raise SystemExit(75)
    time.sleep(.1)
'''


class ManagedUpdates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / 'repo'
        self.root = Path(self.tmp.name) / 'managed'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        (self.repo / 'bin').mkdir()
        shutil.copyfile(ROOT / 'bin/keeper_update.py', self.repo / 'bin/keeper_update.py')
        shutil.copyfile(ROOT / 'bin/repository_auth.py', self.repo / 'bin/repository_auth.py')
        (self.repo / '.gitignore').write_text('__pycache__/\n')
        (self.repo / 'bin/keeper').write_text(KEEPER)
        (self.repo / 'bin/bot').write_text('#!/bin/bash\nexit 0\n')
        self.old = self.commit()
        self.updates = u.initialize(self.root, str(self.repo), 'main')

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return u.git(self.repo, *args)

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-m', 'test release')
        return self.git('rev-parse', 'HEAD')

    def publish(self, keeper=KEEPER, bot='#!/bin/bash\nexit 1\n'):
        (self.repo / 'bin/keeper').write_text(keeper)
        (self.repo / 'bin/bot').write_text(bot)
        return self.commit()

    def test_update_delays_worker_switch_until_ready_and_retains_previous(self):
        new = self.publish()
        self.assertTrue(self.updates.check(force=True))
        self.assertEqual((self.root / 'src').resolve().name, new)
        self.assertEqual((self.root / 'launcher').resolve().name, self.old)
        self.assertEqual(self.updates.read()['previous'], self.old)
        self.assertFalse(self.updates.check(force=True))  # no second update while starting
        self.updates.ready(self.old)
        self.assertTrue(self.updates.read()["pending"])
        self.updates.ready(new)
        self.assertEqual((self.root / 'launcher').resolve().name, new)
        self.assertEqual(self.updates.report()['result'], 'updated')

    def test_disabled_pinned_dirty_invalid_and_offline_leave_current_intact(self):
        new = self.publish()
        self.assertFalse(self.updates.check(enabled=False, force=True))
        self.assertEqual(self.updates.report()['available'], new)
        self.assertFalse(self.updates.check(ref=self.old, force=True))
        (self.root / 'src/bin/bot').write_text('dirty')
        self.assertFalse(self.updates.check(force=True))
        self.assertIn('dirty', self.updates.report()['error'])
        u.git(self.root / 'src', 'checkout', '--', 'bin/bot')
        self.publish(keeper='not valid python !!!')
        self.assertFalse(self.updates.check(force=True))
        self.assertEqual((self.root / 'src').resolve().name, self.old)
        u.git(self.root / 'source', 'remote', 'set-url', 'origin', str(self.repo / 'missing'))
        self.assertFalse(self.updates.check(force=True))
        self.assertEqual(self.updates.report()['result'], 'update failed')

    def test_rollback_quarantines_revision_and_lock_prevents_simultaneous_update(self):
        new = self.publish()
        with (self.root / 'update.lock').open('w') as lock:
            u.fcntl.flock(lock, u.fcntl.LOCK_EX | u.fcntl.LOCK_NB)
            self.assertFalse(self.updates.check(force=True))
        self.assertTrue(self.updates.check(force=True))
        self.updates.rollback('startup failed')
        self.assertEqual(self.updates.read()['failed'], new)
        self.assertEqual((self.root / 'src').resolve().name, self.old)
        self.assertFalse(self.updates.check())  # cadence also prevents restart loops
        state = self.updates.read(); state['checked'] = 0; u.save(self.updates.path, state)
        self.assertFalse(self.updates.check())
        self.assertIn('blocked', self.updates.report()['result'])

    def test_supervisor_recovers_failure_then_updates_without_interrupting_its_worker(self):
        pidfile = self.root / 'worker.pid'
        proc = subprocess.Popen([sys.executable, str(self.root / 'supervisor.py'), 'supervise', '--root', str(self.root)],
                                env={**os.environ, 'TEST_WORKER_PID_FILE': str(pidfile)},
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        worker_pid = None
        try:
            self.wait_for(lambda: pidfile.exists())
            worker_pid = int(pidfile.read_text())
            new = self.publish(keeper='raise RuntimeError("broken startup")\n')
            self.assertTrue(self.updates.check(force=True))
            self.wait_for(lambda: self.updates.report()['result'] == 'rolled back')
            self.assertEqual(self.updates.read()['failed'], new)
            os.kill(worker_pid, 0)
            self.assertIsNone(proc.poll())
            good = self.publish(bot='#!/bin/bash\nexit 2\n')
            self.assertTrue(self.updates.check(force=True))
            self.wait_for(lambda: self.updates.report()['result'] == 'updated')
            self.assertEqual((self.root / 'launcher').resolve().name, good)
            os.kill(worker_pid, 0)
        finally:
            proc.terminate(); proc.wait(8)
            if worker_pid:
                os.kill(worker_pid, signal.SIGTERM)

    def wait_for(self, predicate):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.1)
        self.fail('timed out waiting for supervisor')

    def test_managed_services_use_stable_supervisor_and_preserve_worker_processes(self):
        with patch.dict(os.environ, {'HOME': self.tmp.name, 'BOT_CONFIG': str(self.root / 'config'),
                                     'KEEPER_MANAGED_ROOT': str(self.root)}):
            loader = importlib.machinery.SourceFileLoader('update_test_keeper', str(ROOT / 'bin/keeper'))
            keeper = importlib.util.module_from_spec(importlib.util.spec_from_loader('update_test_keeper', loader))
            loader.exec_module(keeper)
            for platform in ('linux', 'darwin'):
                with self.subTest(platform=platform), patch.object(sys, 'platform', platform), \
                     patch.object(keeper.subprocess, 'run'), patch.object(keeper, 'out', return_value='Linger=yes'):
                    keeper.service(True)
                    path = Path(keeper.UNIT if platform == 'linux' else keeper.PLIST)
                    text = path.read_text()
                    self.assertIn(str(self.root / 'supervisor.py'), text)
                    if platform == 'linux':
                        self.assertIn('KillMode=process', text)
                    else:
                        data = plistlib.loads(path.read_bytes())
                        self.assertTrue(data['AbandonProcessGroup'])
                        self.assertEqual(data['ProgramArguments'][-1], str(self.root))

    def test_legacy_workers_stop_gracefully_once_then_restart_with_same_handle(self):
        with patch.dict(os.environ, {'HOME': self.tmp.name, 'BOT_CONFIG': str(self.root / 'config')}):
            loader = importlib.machinery.SourceFileLoader('migration_test_keeper', str(ROOT / 'bin/keeper'))
            keeper = importlib.util.module_from_spec(importlib.util.spec_from_loader('migration_test_keeper', loader))
            loader.exec_module(keeper)
        hands = keeper.Hands('box')
        want = [{'handle':'legacy', 'harness':'claude', 'run':True, 'set_at':'now', 'set_by':'operator'}]
        seen = [{'handle':'legacy', 'status':'running game'}]
        from unittest.mock import Mock
        proc = Mock(); proc.poll.return_value = None
        with patch.object(keeper, 'managed_root', return_value=str(self.root)), \
             patch.object(hands, 'spawn', return_value=proc) as spawn:
            hands.reconcile(want, seen)
            self.assertEqual(spawn.call_args.args, ('legacy', [keeper.BOT, 'stop', 'legacy']))
            hands.reconcile(want, seen)
            self.assertEqual(spawn.call_count, 1)
            proc.poll.return_value = 0
            hands.reconcile(want, [{'handle':'legacy', 'status':'stopped'}])
            self.assertEqual(spawn.call_args.args, ('legacy', [keeper.BOT, 'claude', '--name', 'legacy']))

    def test_real_keeper_checks_and_reports_new_revision_automatically(self):
        shutil.copyfile(ROOT / 'bin/keeper', self.repo / 'bin/keeper')
        shutil.copyfile(ROOT / 'bin/bot', self.repo / 'bin/bot')
        os.chmod(self.repo / 'bin/bot', 0o755)
        os.chmod(self.repo / 'bin/keeper', 0o755)
        old = self.commit()
        self.assertTrue(self.updates.check(force=True))
        self.updates.ready(old)
        reports = []
        class Board(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def respond(self, body, code=200):
                data = json.dumps(body).encode()
                self.send_response(code); self.send_header('Content-Length', str(len(data))); self.end_headers()
                self.wfile.write(data)
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.respond({'token':'test-only-token'}, 201)
            def do_GET(self):
                self.respond({'agents':[]})
            def do_PUT(self):
                reports.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.respond({'want':[], 'secrets':''})
        board = ThreadingHTTPServer(('127.0.0.1', 0), Board)
        threading.Thread(target=board.serve_forever, daemon=True).start()
        home = self.root / 'home'; home.mkdir()
        bindir = self.root / 'tools'; bindir.mkdir()
        gh = bindir / 'gh'; gh.write_text('#!/bin/sh\nexit 0\n'); gh.chmod(0o755)
        env = {**os.environ, 'HOME':str(home), 'BOT_CONFIG':str(home / 'config'),
               'BOARD':'http://127.0.0.1:' + str(board.server_port), 'KEEPER_HOST':'update-test',
               'PATH':str(bindir) + os.pathsep + os.environ['PATH']}
        env.pop('KEEPER_MANAGED_ROOT', None)
        env.pop('KEEPER_AUTO_UPDATE', None)
        env.pop('KEEPER_UPDATE_PIN', None)
        logpath = self.root / 'integration.log'
        with logpath.open('wb') as log:
            proc = subprocess.Popen([sys.executable, str(self.root / 'supervisor.py'), 'supervise', '--root', str(self.root)],
                                    env=env, stdout=log, stderr=log)
            try:
                self.wait_for(lambda: bool(reports))
                token = home / '.config/bot_board/keeper-update-test.token'
                self.assertEqual(token.read_text().strip(), 'test-only-token')
                (self.repo / 'new-release.txt').write_text('publish newer commit')
                new = self.commit()
                state = self.updates.read(); state['checked'] = 0; u.save(self.updates.path, state)
                end = time.monotonic() + 25
                while time.monotonic() < end and not any(r.get('updates', {}).get('installed') == new for r in reports):
                    time.sleep(.1)
                self.assertTrue(any(r.get('updates', {}).get('installed') == new for r in reports), logpath.read_text())
                self.assertEqual(token.read_text().strip(), 'test-only-token')
                self.assertEqual((self.root / 'launcher').resolve().name, new)
            finally:
                proc.terminate(); proc.wait(8)
                board.shutdown(); board.server_close()

    def test_existing_clean_managed_checkout_migrates_without_touching_external_state(self):
        other = Path(self.tmp.name) / 'old-managed'
        other.mkdir()
        subprocess.run(['git', 'clone', str(self.repo), str(other / 'src')], check=True, capture_output=True)
        (self.repo / 'bin/keeper_update.py').unlink()
        legacy = self.commit()
        u.git(other / 'src', 'fetch', 'origin', 'main')
        u.git(other / 'src', 'checkout', '--detach', legacy)
        shutil.copyfile(ROOT / 'bin/keeper_update.py', self.repo / 'bin/keeper_update.py')
        latest = self.commit()
        token = other / 'token'; token.write_text('local-test-state')
        manager = u.initialize(other, str(self.repo), 'main')
        self.assertEqual(token.read_text(), 'local-test-state')
        self.assertTrue((other / 'initial-checkout/.git').is_dir())
        self.assertTrue((other / 'src').is_symlink())
        self.assertEqual(manager.report()['installed'], latest)
        self.assertEqual(manager.report()['previous'], legacy)
        self.assertTrue(manager.read()['pending'])


if __name__ == '__main__':
    unittest.main()
