"""Explicit-path Kanban inspection without booting Hermes or initializing a store.

Run with: python -m hermes_cli.kanban_inspect --database PATH --board SLUG TASK...
Use --census instead of TASK arguments for content-free task-state counts.
The output contains private card content; retain it only at an approved destination.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys

from hermes_cli.sqlite_readonly import readonly_snapshot


def inspect_census(database: Path, board: str) -> dict:
    """Count stored task states without reading card bodies or initializing Hermes."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', board):
        raise ValueError('provide a valid board label')
    database = database.resolve()
    with readonly_snapshot(database) as conn:
        counts = {row['status']: row['count'] for row in conn.execute(
            'SELECT status, COUNT(*) AS count FROM tasks GROUP BY status ORDER BY status')}
        return dict(database=str(database), board_label=board,
                    sqlite_version=sqlite3.sqlite_version,
                    inspection_only=True, admission_granted=False,
                    counts=counts, total=sum(counts.values()),
                    non_archived=sum(count for status, count in counts.items() if status != 'archived'))


def inspect_tasks(database: Path, board: str, task_ids: list[str]) -> dict:
    """Return full revision inputs for at most twenty cards in one read snapshot.

    Board is a caller-supplied label, not an assertion of canonical-path custody.
    Missing schema/cards fail without repair and no partial result is returned.
    """
    from hermes_cli.kanban_task_revision import task_revision
    from hermes_cli.kanban_transfer import _selected_task_rows

    if (not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', board)
            or not 1 <= len(task_ids) <= 20
            or len(set(task_ids)) != len(task_ids)
            or any(not re.fullmatch(r't_[0-9a-f]{8}', tid) for tid in task_ids)):
        raise ValueError('provide a board label and one to twenty distinct task IDs')
    database = database.resolve()
    with readonly_snapshot(database) as conn:
        remaining_bytes = 8 * 1024 * 1024
        cards = []
        for tid in task_ids:
            rows = _selected_task_rows(conn, [tid], max_bytes=remaining_bytes)
            remaining_bytes -= len(json.dumps(rows, ensure_ascii=False).encode("utf-8"))
            if remaining_bytes < 0:
                raise ValueError("inspection exceeds size limit")
            cards.append(dict(task_id=tid, revision=task_revision(board, tid, rows), rows=rows))
        return dict(database=str(database), board_label=board,
                    sqlite_version=sqlite3.sqlite_version,
                    sqlite_source_id=conn.execute('SELECT sqlite_source_id()').fetchone()[0],
                    inspection_only=True, admission_granted=False, cards=cards)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--board', required=True)
    parser.add_argument('--census', action='store_true', help='count task states without reading card content')
    parser.add_argument('task_ids', nargs='*')
    args = parser.parse_args(argv)
    if args.census and args.task_ids:
        parser.error('--census cannot be combined with task IDs')
    if not args.census and not args.task_ids:
        parser.error('provide task IDs or --census')
    try:
        result = (inspect_census(args.database, args.board) if args.census
                  else inspect_tasks(args.database, args.board, args.task_ids))
    except (sqlite3.Error, OSError, ValueError) as error:
        print(json.dumps(dict(error=type(error).__name__, message=str(error),
                              sqlite_version=sqlite3.sqlite_version,
                              sqlite_errorcode=getattr(error, 'sqlite_errorcode', None),
                              sqlite_errorname=getattr(error, 'sqlite_errorname', None))), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
