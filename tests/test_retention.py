"""Local retention checks; all files and GC identities live in temporary homes."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class Retention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.cfg = self.home / '.config/bot_board'
        self.cfg.mkdir(parents=True)
        self.env = {**os.environ, 'HOME': str(self.home), 'BOT_CONFIG': str(self.home / 'config'),
                    'BOARD': 'http://unused.invalid'}
        loader = importlib.machinery.SourceFileLoader('retention_keeper', str(ROOT / 'bin/keeper'))
        self.keeper = importlib.util.module_from_spec(importlib.util.spec_from_loader('retention_keeper', loader))
        with patch.dict(os.environ, self.env):
            loader.exec_module(self.keeper)

    def test_rotation_bounds_archives_and_keeps_open_append_descriptor(self):
        log = self.cfg / 'log'
        with log.open('ab') as writer:
            for value in [b'a', b'b', b'c', b'd', b'e']:
                writer.write(value * 30)
                writer.flush()
                inode = log.stat().st_ino
                self.keeper.rotate_log(log, max_bytes=10)
                self.assertEqual(log.stat().st_ino, inode)
                self.assertEqual(log.read_bytes(), b'')
                self.assertEqual(Path(str(log) + '.1').read_bytes(), value * 10)
            writer.write(b'next')
        self.assertEqual(log.read_bytes(), b'next')
        self.assertEqual([Path(str(log) + '.' + str(n)).read_bytes() for n in [1, 2, 3]],
                         [b'e' * 10, b'd' * 10, b'c' * 10])
        self.assertFalse(Path(str(log) + '.4').exists())
        self.assertEqual(Path(str(log) + '.1').stat().st_mode & 0o777, 0o600)
        self.keeper.rotate_log(log, max_bytes=10)
        self.keeper.rotate_log(self.cfg / 'missing', max_bytes=10)
        self.assertEqual(log.read_bytes(), b'next')

    def test_failed_archive_does_not_truncate_original(self):
        log = self.cfg / 'log'
        log.write_bytes(b'important error output')
        open_file = os.open
        def fail_archive(path, flags, *args):
            if str(path).endswith('.1'):
                raise PermissionError('read-only archive')
            return open_file(path, flags, *args)
        with patch.object(self.keeper.os, 'open', side_effect=fail_archive):
            with self.assertRaises(PermissionError):
                self.keeper.rotate_log(log, max_bytes=10)
        self.assertEqual(log.read_bytes(), b'important error output')

    def test_rotation_refuses_symlink_sources_and_does_not_follow_archive_symlinks(self):
        target = self.cfg / 'keep'
        target.write_bytes(b'keep this content')
        log = self.cfg / 'log'
        log.symlink_to(target)
        with self.assertRaises(OSError):
            self.keeper.rotate_log(log, max_bytes=10)
        self.assertEqual(target.read_bytes(), b'keep this content')
        log.unlink()
        log.write_bytes(b'large log output')
        Path(str(log) + '.1').symlink_to(target)
        self.keeper.rotate_log(log, max_bytes=10)
        self.assertEqual(target.read_bytes(), b'keep this content')

    def test_daily_gc_persists_schedule_across_keeper_restarts(self):
        maintenance = self.keeper.Maintenance()
        with patch.object(self.keeper.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            maintenance.tick([])
            self.assertEqual(run.call_args.args[0], [self.keeper.BOT, 'gc'])
            self.assertTrue(maintenance.stamp.exists())
            maintenance.tick([])
            self.keeper.Maintenance().tick([])
            self.assertEqual(run.call_count, 1)
            yesterday = time.time() - 86401
            os.utime(maintenance.stamp, (yesterday, yesterday))
            maintenance.tick([])
            self.assertEqual(run.call_count, 2)

    def test_gc_failure_is_reported_and_retried_after_five_minutes(self):
        maintenance = self.keeper.Maintenance()
        failure = subprocess.CalledProcessError(1, ['bot', 'gc'], stderr='gc refused')
        with patch.object(self.keeper.subprocess, 'run', side_effect=failure) as run, \
                patch.object(self.keeper.time, 'time', return_value=1000000) as clock, \
                patch('sys.stderr') as stderr:
            maintenance.tick([])
            maintenance.tick([])
            self.assertEqual(run.call_count, 1)
            self.assertFalse(maintenance.stamp.exists())
            self.assertIn('gc refused', str(stderr.write.call_args_list))
            clock.return_value += 300
            maintenance.tick([])
            self.assertEqual(run.call_count, 2)

    def test_keeper_leaves_running_bot_logs_to_the_launcher(self):
        maintenance = self.keeper.Maintenance()
        maintenance.stamp.touch()
        rows = [{'handle': 'ada', 'status': 'running game'},
                {'handle': 'milo', 'status': 'waiting'},
                {'handle': 'opal', 'status': 'stopped'}]
        with patch.object(self.keeper, 'rotate_log') as rotate:
            maintenance.tick(rows)
        self.assertEqual([call.args[0] for call in rotate.call_args_list],
                         [self.cfg / 'keeper.log', self.cfg / 'bots/opal/log',
                          self.cfg / 'bots/opal/keeper.out'])

    def test_cli_and_launcher_rotation_are_outside_harness_error_detection(self):
        log = self.cfg / 'log'
        with log.open('wb') as output:
            output.truncate(10 * 1024 * 1024)
        subprocess.run([os.sys.executable, str(ROOT / 'bin/keeper'), 'rotate-logs', str(log)],
                       env=self.env, check=True, capture_output=True, text=True)
        self.assertEqual(log.stat().st_size, 0)
        self.assertEqual(Path(str(log) + '.1').stat().st_size, 10 * 1024 * 1024)
        source = (ROOT / 'bin/bot').read_text()
        harness = source.split('run_harness() {', 1)[1].split('\n# ---- where a message', 1)[0]
        self.assertNotIn('rotate_logs', harness)
        self.assertIn('while :; do\n  rotate_logs\n', source)

    def test_gc_removes_only_stale_sessions_and_preserves_named_identities(self):
        old = time.time() - 10 * 86400
        for handle in ['box-project-abcd', 'box-project-1234', 'ada', 'ada-abcd', 'keeper-box-abcd']:
            token = self.cfg / (handle + '.token')
            token.write_text('test-token')
            os.utime(token, (old, old))
        for handle in ['ada', 'ada-abcd']:
            state = self.cfg / 'bots' / handle
            state.mkdir(parents=True)
            (state / 'harness').write_text('copilot')
            (state / 'session').write_text('saved-conversation')
            for path in [state, *state.iterdir()]:
                os.utime(path, (old, old))
        recent = self.cfg / 'box-project-1234.token'
        recent.touch()
        bindir = self.home / 'bin'
        bindir.mkdir()
        curl = bindir / 'curl'
        roster = json.dumps({'agents': [{'handle': 'ada', 'last_seen_at': None}]})
        curl.write_text('#!/bin/sh\nprintf \'%s\\n\' \'' + roster + '\'\n')
        curl.chmod(0o755)
        env = {**self.env, 'PATH': str(bindir) + os.pathsep + os.environ['PATH'], 'BOT_GC_DAYS': '7'}
        for flags in [['--dry-run'], []]:
            result = subprocess.run([str(ROOT / 'bin/bot'), 'gc', *flags], env=env, check=True,
                                    capture_output=True, text=True)
            self.assertIn('box-project-abcd.token', result.stdout)
            self.assertEqual((self.cfg / 'box-project-abcd.token').exists(), bool(flags))
            for handle in ['box-project-1234', 'ada', 'ada-abcd', 'keeper-box-abcd']:
                self.assertTrue((self.cfg / (handle + '.token')).exists())
            self.assertEqual((self.cfg / 'bots/ada-abcd/session').read_text(), 'saved-conversation')
        self.assertTrue((self.cfg / 'gc.lock').exists())


if __name__ == '__main__':
    unittest.main()
