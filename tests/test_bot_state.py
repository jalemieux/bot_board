"""One vocabulary for what a bot is doing, shared by /fleet, /projects and /status (#100).
Run with python -m unittest discover -s tests."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.fleet import bot_state


class BotState(unittest.TestCase):
    def check(self, bot, state, can_work, words=None, reporting=True):
        got = bot_state(bot, reporting)
        self.assertEqual((got['state'], got['can_work']), (state, can_work), got)
        if words:
            self.assertEqual(got['words'], words)
        return got

    def test_stopped_launcher_retains_usage_reason(self):
        reason = 'out of claude usage since yesterday: weekly limit; resets Monday'
        got = self.check({'status': 'stopped', 'want': 'running', 'health': reason,
                          'problem': 'harness exited 1'}, 'out of usage', False)
        self.assertEqual(got['reason'], reason)
        self.check({'status': 'stopped', 'want': 'stopped', 'health': reason}, 'stopped', False)

    def test_capacity_retains_reason(self):
        reason = 'out of codex capacity since yesterday: model at capacity; retried every 10 min'
        for status in ('waiting', 'stopped'):
            got = self.check({'status': status, 'want': 'running', 'health': reason},
                             'at capacity', False, 'model temporarily at capacity')
            self.assertEqual(got['reason'], reason)

    def test_a_start_after_the_outage_shows_starting(self):
        # #183: the launcher probes its harness as it comes up, so the old outage is not news
        bot = {'status': 'stopped', 'want': 'running', 'set_at': '2026-10-19T20:00:00+00:00',
               'health': 'out of claude usage since 2026-10-16T15:16:00Z: hit your weekly limit'}
        self.check(bot, 'starting', False)
        self.check({**bot, 'set_at': '2026-10-16T10:00:00+00:00'}, 'out of usage', False)
        self.check({**bot, 'status': 'waiting'}, 'out of usage', False)   # up: its own word

    def test_every_state(self):
        self.check({'status': 'running wordsnap'}, 'working', True, 'working on wordsnap')
        self.check({'status': 'waiting'}, 'waiting', True, 'waiting for work')
        self.check({'status': 'waiting', 'health': 'last run failed (exit 1)'}, 'failing', True)
        self.check({'status': 'waiting', 'health': 'logged out of claude since …'}, 'needs login', False)
        self.check({'status': 'waiting', 'health': 'out of claude usage since …'}, 'out of usage', False)
        self.check({'status': 'running x, stopping'}, 'stopping', False, 'stopping')
        self.check({'status': 'waiting', 'want': 'stopped', 'kill': 'now'}, 'stopping', False, 'kill asked')
        self.check({'status': 'waiting', 'want': 'stopped'}, 'stopping', False, 'stop asked')
        self.check({'status': 'running x, stopping', 'want': 'stopped', 'kill': 'now'}, 'stopping', False,
                   'stopping, kill asked')
        self.check({'status': 'stopped', 'want': 'running'}, 'starting', False)
        self.check({'status': 'stopped', 'want': 'running', 'problem': 'codex is not installed'}, 'failing', False)
        self.check({'status': 'waiting', 'move_to': 'beta'}, 'moving', False, 'moving to beta: stopping')
        self.check({'status': 'stopped', 'fetch': 'a.tgz'}, 'moving', False, 'arriving: unpacking')
        self.check({'status': 'stopped'}, 'stopped', False)
        self.check({'status': 'moved'}, 'stopped', False, 'moved to another box')
        self.check({'status': 'waiting'}, 'unknown', False, 'waiting when last heard', reporting=False)


if __name__ == '__main__':
    unittest.main()
