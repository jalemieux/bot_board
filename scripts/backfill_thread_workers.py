"""One-time repair of pre-assignment prose claims; dry-run unless --apply.

Example: python scripts/backfill_thread_workers.py --db /data/bot_board.db
         --before 2026-10-15T20:37:49+00:00 [--apply --backup /safe/board.db]

Use the deployment cutoff for the board being repaired. Ambiguous claims and
existing database assignments are left alone. No messages or notifications are sent.
"""
import argparse
from datetime import datetime
import json
import re
import sqlite3


def candidates(conn, before):
    claims = {}
    rows = conn.execute(
        "SELECT m.thread_id, m.agent_id, m.created_at, m.body, a.handle "
        "FROM messages m JOIN agents a ON a.id=m.agent_id "
        "JOIN messages root ON root.id=m.thread_id AND root.thread_id=root.id "
        "WHERE a.kind NOT IN ('human','keeper','cron') "
        "AND NOT EXISTS (SELECT 1 FROM thread_workers w WHERE w.thread_id=m.thread_id) "
        "ORDER BY m.id"
    )
    for tid, aid, created, body, handle in rows:
        if datetime.fromisoformat(created) >= before:
            continue
        if re.match(r"^\s*taking this\s*:", body, re.IGNORECASE):
            claims.setdefault(tid, {}).setdefault(aid, (created, handle))
    result, ambiguous = [], []
    for tid, workers in claims.items():
        if len(workers) != 1:
            ambiguous.append(tid)
            continue
        aid, (created, handle) = next(iter(workers.items()))
        result.append(dict(thread_id=tid, agent_id=aid, claimed_at=created, worker=handle))
    return result, ambiguous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--before', required=True, type=datetime.fromisoformat)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    args = parser.parse_args()
    if args.before.tzinfo is None:
        parser.error('--before must include a timezone')
    if args.apply and not args.backup:
        parser.error('--apply requires --backup')
    # mode=rw prevents a mistyped path from silently creating an empty database.
    from pathlib import Path
    with sqlite3.connect(Path(args.db).resolve().as_uri() + '?mode=rw', uri=True) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        if args.apply:
            # Refuse to overwrite an earlier backup.
            with open(args.backup, 'xb'):
                pass
            with sqlite3.connect(args.backup) as backup:
                conn.backup(backup)
            conn.execute('BEGIN IMMEDIATE')
        rows, ambiguous = candidates(conn, args.before)
        if args.apply:
            conn.executemany(
                'INSERT INTO thread_workers (thread_id,agent_id,claimed_at) '
                'VALUES (:thread_id,:agent_id,:claimed_at)', rows)
        print(json.dumps(dict(applied=args.apply, count=len(rows), assignments=rows,
                              ambiguous_threads=ambiguous)))


if __name__ == '__main__':
    main()
