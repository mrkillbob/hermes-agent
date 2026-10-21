"""Project identity selects audit destination and dedup scope, never the caller cwd."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from hermes_cli import projects_db

SCRIPT = Path(__file__).resolve().parents[2] / 'hermes_cli' / 'kanban_audit_enqueue.py'
pytestmark = pytest.mark.platforms('posix')


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    with projects_db.connect_closing(tmp_path / 'projects.db') as conn:
        ids = {name: projects_db.create_project(conn, name=name, slug=name, board_slug=board)
               for name, board in [('hermes-agent', 'default'), ('lunabot', 'tradingbot-burndown'),
                                   ('another-project', 'default'), ('unbound', None)]}
    from hermes_cli import kanban_db
    for slug, name in [('default', 'hermes-agent'), ('tradingbot-burndown', 'lunabot')]:
        kanban_db.write_board_metadata(slug, project_id=ids[name])
    body = tmp_path / 'audit body.txt'
    body.write_text('Read-only audit; keep project context.\nNo source changes.\n')
    fake = tmp_path / 'fake hermes'
    fake.write_text('#!' + sys.executable + '\n' + '''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['AUDIT_TEST_CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
if 'list' in args: print(Path(os.environ['AUDIT_TEST_TASKS']).read_text())
else: print(json.dumps({'id':'t_created','project_id':args[args.index('--project')+1]}))
''')
    fake.chmod(0o700)
    tasks = tmp_path / 'tasks.json'
    tasks.write_text('[]')
    log = tmp_path / 'calls.jsonl'
    env = dict(os.environ, HERMES_HOME=str(tmp_path), AUDIT_TEST_CALLS=str(log), AUDIT_TEST_TASKS=str(tasks))
    def run(project='hermes-agent', *extra):
        result = subprocess.run([sys.executable, str(SCRIPT), '--hermes', str(fake),
                                 '--project', project, '--assignee', 'auditor', '--creator', 'weekly-audit',
                                 '--title', 'Audit', '--idempotency-key', 'week-key', '--max-runtime', '6m',
                                 '--body-file', str(body), *extra], env=env, cwd=tmp_path,
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                check=False, timeout=20)
        calls = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        return result, calls
    return run, ids, tasks


@pytest.mark.parametrize(('project','board'), [('hermes-agent','default'), ('lunabot','tradingbot-burndown')])
def test_registered_project_selects_every_command_board(setup, project, board):
    run, ids, _ = setup
    result, calls = run(project)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 2
    assert all(args[args.index('--board') + 1] == board for args in calls)
    create = calls[-1]
    assert create[create.index('--project') + 1] == ids[project]
    assert ids[project] in create[create.index('--idempotency-key') + 1]
    assert '--body-file' in create and '--body' not in create


@pytest.mark.parametrize(('project','extra','message'), [
    ('missing', [], 'unknown project'), ('unbound', [], 'board binding'),
    ('hermes-agent', ['--board','tradingbot-burndown'], 'board does not match'),
])
def test_invalid_identity_is_rejected_before_any_cli_call(setup, project, extra, message):
    run, _, _ = setup
    result, calls = run(project, *extra)
    assert result.returncode != 0
    assert message in result.stderr
    assert calls == []


@pytest.mark.parametrize(('owner','status','expected_calls'), [('hermes-agent','ready',1),
    ('another-project','ready',2), ('hermes-agent','done',2), ('hermes-agent','archived',2)])
def test_deduplication_is_project_specific(setup, owner, status, expected_calls):
    run, ids, tasks = setup
    tasks.write_text(json.dumps([{'id':'t_existing','project_id':ids[owner],
                                  'created_by':'weekly-audit','status':status}]))
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert len(calls) == expected_calls


def test_projectless_legacy_audit_needs_reconciliation_not_duplicate(setup):
    run, _, tasks = setup
    tasks.write_text(json.dumps([{'id':'t_legacy','project_id':None,
                                  'created_by':'weekly-audit','status':'ready'}]))
    result, calls = run()
    assert result.returncode != 0
    assert 'active audit has no project binding' in result.stderr
    assert len(calls) == 1


@pytest.mark.parametrize('owner', [None, 'another-project'])
def test_unowned_or_rebound_board_refuses_before_cli(setup, monkeypatch, tmp_path, owner):
    from hermes_cli import kanban_db
    run, ids, _ = setup
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    kanban_db.write_board_metadata('default', project_id=ids[owner] if owner else '')
    result, calls = run()
    assert result.returncode != 0
    assert 'board ownership' in result.stderr
    assert calls == []


def test_archived_board_refuses_before_cli(setup):
    from hermes_cli import kanban_db
    run, _, _ = setup
    kanban_db.write_board_metadata('default', archived=True)
    result, calls = run()
    assert result.returncode != 0
    assert 'archived board' in result.stderr
    assert calls == []
