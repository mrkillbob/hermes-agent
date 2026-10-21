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
    assert result['argv'][:2] == ['--run-module', 'hermes_cli.kanban_audit_enqueue']
    assert result['argv'][result['argv'].index('--project') + 1] == 'hermes-agent'
    assert result['body'] == args[-1]
    assert not Path(result['path']).exists()
