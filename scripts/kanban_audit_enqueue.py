"""Enqueue a recurring audit on its registered project's board, never an ambient board.

Used by installed audit wrappers. Pass the existing project id/slug explicitly;
missing, archived or unbound registrations require operator reconciliation.
Existing project linking selects a repository worktree when a primary repo exists;
otherwise the existing CLI uses scratch. This producer never changes that policy.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys


def resolve_project(identity: str, board: str | None):
    from hermes_cli import projects_db

    registry = projects_db.projects_db_path()
    with closing(sqlite3.connect(registry.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        project = projects_db.get_project(conn, identity)
    if project is None:
        raise ValueError('unknown project; use an existing registered id or slug')
    if project.archived:
        raise ValueError('archived project cannot receive a recurring audit')
    if not project.board_slug:
        raise ValueError('project requires an explicit registered board binding')
    destination = projects_db.normalize_slug(project.board_slug)
    if destination != project.board_slug:
        raise ValueError('invalid project board binding')
    if board is not None and board != destination:
        raise ValueError('requested board does not match the project board binding')
    return project


def run_json(command: list[str]):
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=60)
    return json.loads(result.stdout)


def enqueue(args):
    project = resolve_project(args.project, args.board)
    if not args.body_file.is_file():
        raise ValueError('audit body file is missing')
    from hermes_cli.active_sessions import _FileLock
    from hermes_cli import projects_db

    # Only this producer's same-project list/create sequence is serialized.
    # Canonical ids make slug and id callers share ownership; other projects
    # and profiles retain independent execution.
    key = hashlib.sha256(project.id.encode('utf-8')).hexdigest()
    lock = projects_db.projects_db_path().parent / 'runtime' / 'audit-enqueue' / (key + '.lock')
    with _FileLock(lock):
        current = resolve_project(project.id, args.board)
        return enqueue_registered(args, current)


def enqueue_registered(args, project):
    command = [args.hermes, 'kanban', '--board', project.board_slug]
    tasks = run_json([*command, 'list', '--assignee', args.assignee, '--json'])
    if not isinstance(tasks, list) or any(not isinstance(task, dict) for task in tasks):
        raise ValueError('invalid audit task listing')
    for task in tasks:
        if task.get('created_by') != args.creator or task.get('status') in {'done', 'archived'}:
            continue
        if not task.get('project_id'):
            raise ValueError('active audit has no project binding; reconcile it before enqueueing')
        if task['project_id'] == project.id:
            return {'status': 'idle', 'reason': 'audit_already_active',
                    'project_id': project.id, 'board': project.board_slug, 'task_id': task.get('id')}
    result = run_json([
        *command, 'create', args.title, '--body-file', str(args.body_file),
        '--assignee', args.assignee, '--project', project.id, '--workspace', 'scratch',
        '--idempotency-key', f'{project.id}:{args.idempotency_key}',
        '--max-runtime', args.max_runtime, '--max-retries', '2',
        '--created-by', args.creator, '--json',
    ])
    if not isinstance(result, dict) or result.get('project_id') != project.id:
        # A create may already have happened. Never delete, retry or create a replacement.
        raise ValueError('created audit project binding was not confirmed; inspect the original outcome')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('hermes', 'project', 'assignee', 'creator', 'title', 'idempotency-key', 'max-runtime'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--body-file', type=Path, required=True)
    parser.add_argument('--board', help='Optional expected board; must match the registered project')
    args = parser.parse_args()
    try:
        result = enqueue(args)
    except (ValueError, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f'audit enqueue failed: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
