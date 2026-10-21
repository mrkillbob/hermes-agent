"""The audit adapter invokes an installed package, preserving explicit project/body."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.platforms('posix')


def test_compatibility_adapter_requires_project_and_passes_body_file(tmp_path):
    import shutil
    adapter = Path(__file__).resolve().parents[2] / 'scripts' / 'enqueue-kanban-audit.sh'
    binary = tmp_path / 'hermes'
    binary.write_text('#!' + sys.executable + '\n' + '''import json,sys
from pathlib import Path
args=sys.argv[1:]
body=Path(args[args.index('--body-file')+1])
print(json.dumps({'argv':args,'body':body.read_text(),'path':str(body)}))
''')
    binary.chmod(0o700)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'])
    shell = shutil.which('sh')
    args = ['auditor', 'creator', 'title', 'key', '6m', 'body with spaces\nand newline']
    refused = subprocess.run([shell, str(adapter), *args], check=False,
                             capture_output=True, text=True, encoding='utf-8', env=env, timeout=20)
    assert refused.returncode == 64
    accepted = subprocess.run([shell, str(adapter), *args, 'hermes-agent'], check=False,
                              capture_output=True, text=True, encoding='utf-8', env=env, timeout=20)
    assert accepted.returncode == 0, accepted.stderr
    result = json.loads(accepted.stdout)
    assert result['argv'][:2] == ['kanban', 'enqueue-audit']
    assert result['argv'][result['argv'].index('--project') + 1] == 'hermes-agent'
    assert result['body'] == args[-1]
    assert not Path(result['path']).exists()


@pytest.mark.parametrize('board', ['default', 'tradingbot-burndown'])
def test_adapter_uses_real_public_console_entrypoint(tmp_path, monkeypatch, board):
    """A wheel/Nix console script calls main(), not the private store launcher."""
    import shutil
    from hermes_cli import projects_db, kanban_db, kanban_db_connect
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: home)
    with projects_db.connect_closing(home / 'projects.db') as conn:
        project = projects_db.create_project(conn, name='audit-project', board_slug=board)
    kanban_db.write_board_metadata(board, project_id=project)
    root = Path(__file__).resolve().parents[2]
    binary = tmp_path / 'hermes'
    binary.write_text('#!' + sys.executable + '\n' +
                      f'import sys; sys.path.insert(0, {str(root)!r})\n' +
                      'from hermes_cli.main import main\nmain()\n')
    binary.chmod(0o700)
    env = dict(os.environ, HOME=str(home), HERMES_HOME=str(home), HERMES_KANBAN_HOME=str(home),
               PATH=str(tmp_path) + os.pathsep + os.environ['PATH'])
    args = [shutil.which('sh'), str(root / 'scripts/enqueue-kanban-audit.sh'),
            'default', 'fixture-audit', 'Console audit', 'fixture-key', '6m',
            'Public console body\nwith newline', project]
    result = subprocess.run(args, env=env, cwd=tmp_path, capture_output=True,
                            text=True, encoding='utf-8', check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    created = json.loads(result.stdout)
    assert created['project_id'] == project
    with kanban_db_connect.connect_closing(board=board) as conn:
        task = kanban_db.get_task(conn, created['id'])
        assert task.body == args[-2]
        assert task.assignee == 'default'
    refused = subprocess.run(args, env=dict(env, HERMES_DELEGATED_CHILD_CONTEXT='1'),
                             cwd=tmp_path, capture_output=True, text=True,
                             encoding='utf-8', check=False, timeout=60)
    assert refused.returncode == 1 and 'delegate_task' in refused.stderr
