"""The legacy repair must not guess between claimants or replace live ownership."""
from datetime import datetime
import sqlite3
import unittest

from scripts.backfill_thread_workers import candidates


class LegacyWorkers(unittest.TestCase):
    def test_only_unambiguous_historical_claims_are_recovered(self):
        with sqlite3.connect(':memory:') as conn:
            conn.executescript('''
                CREATE TABLE agents (id INTEGER, handle TEXT, kind TEXT);
                CREATE TABLE messages (id INTEGER, thread_id INTEGER, agent_id INTEGER,
                                       created_at TEXT, body TEXT);
                CREATE TABLE thread_workers (thread_id INTEGER PRIMARY KEY, agent_id INTEGER);
                INSERT INTO agents VALUES (1,'first','codex'),(2,'second','claude-code'),(3,'human','human');
                INSERT INTO thread_workers VALUES (30,2);
            ''')
            old = '2026-10-14T00:00:00+00:00'
            new = '2026-10-16T00:00:00+00:00'
            for tid in (10, 20, 30, 40, 50, 60):
                conn.execute('INSERT INTO messages VALUES (?,?,3,?,?)', (tid, tid, old, 'Request'))
            for mid, tid, aid, when, body in (
                (11, 10, 1, old, 'taking this: review'),
                (12, 10, 1, old, 'TAKING THIS: follow-up'),
                (21, 20, 1, old, 'taking this: part one'),
                (22, 20, 2, old, 'taking this: part two'),
                (31, 30, 1, old, 'taking this: existing owner wins'),
                (41, 40, 1, new, 'taking this: after rollout'),
                (51, 50, 3, old, 'taking this: human'),
                (61, 60, 1, old, 'Someone said taking this: review'),
            ):
                conn.execute('INSERT INTO messages VALUES (?,?,?,?,?)', (mid, tid, aid, when, body))
            before = datetime.fromisoformat('2026-10-15T20:37:49+00:00')
            rows, ambiguous = candidates(conn, before)
            self.assertEqual(rows, [dict(thread_id=10, agent_id=1, claimed_at=old, worker='first')])
            self.assertEqual(ambiguous, [20])
            conn.execute('INSERT INTO thread_workers VALUES (10,1)')
            self.assertEqual(candidates(conn, before), ([], [20]))


if __name__ == '__main__':
    unittest.main()
