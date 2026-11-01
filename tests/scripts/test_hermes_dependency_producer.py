"""The Hermes audit producer pins its destination and preserves its bounded brief."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.platforms('posix')

@pytest.mark.parametrize('exit_code', [0, 7])
def test_producer_preserves_scope_and_refusal(tmp_path, exit_code):
    wrapper = tmp_path / 'producer.sh'
    shutil.copyfile(ROOT / 'scripts/enqueue-hermes-dependency-health.sh', wrapper)
    fake = tmp_path / 'hermes'
    fake.write_text('#!' + sys.executable + '\n' + '''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
body=Path(args[args.index('--body-file')+1])
with open(os.environ['RECORD'], 'a') as out:
    out.write(json.dumps(dict(args=args,body=body.read_text(),body_file=str(body)))+'\\n')
sys.exit(int(os.environ['FAKE_EXIT']))
''')
    fake.chmod(0o700)
    record = tmp_path / 'record.jsonl'
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               RECORD=str(record), FAKE_EXIT=str(exit_code), TMPDIR=str(tmp_path))
    for _ in range(2):
        result = subprocess.run(['/bin/sh', str(wrapper)], env=env, capture_output=True,
                                text=True, timeout=10, check=False)
        assert result.returncode == exit_code, result.stderr
    rows = [json.loads(line) for line in record.read_text().splitlines()]
    assert len(rows) == 2  # One call each, including when the supported CLI refuses.
    expected = 'Audit Python deps (pyproject.toml upper bounds), Node deps (package.json), GitHub Actions SHA pins, and local tooling (serena, tirith, etc.) for staleness or known CVEs. File Kanban tasks for updates under board \'default\'. Read-only.\n\n\ndelivery: When you find something actionable, create exactly one Kanban task: `hermes kanban create "<title>" --assignee task-intake-router --body "<details>" --priority <low|medium|high>`. Always assign to task-intake-router — it will route to the right profile. Do NOT assign to any other profile directly. If nothing actionable is found, report OK and create no card.\\n\\nBOARD POLICY: This job owns the \'default\' Kanban board. Pin every Kanban command/tool call to that board explicitly; never use the current-board default and never create or target another board.'
    keys = []
    for row in rows:
        assert row['args'][:2] == ['kanban', 'enqueue-audit']
        options = dict(zip(row['args'][2::2], row['args'][3::2]))
        assert options['--board'] == 'default'
        assert options['--project'] == 'p_2a482075'
        assert options['--assignee'] == 'hermes-maintenance-steward'
        assert options['--creator'] == 'cron-hermes-dependency-health'
        assert options['--max-runtime'] == '15m'
        assert row['body'] == expected
        assert not Path(row['body_file']).exists()
        keys.append(options['--idempotency-key'])
    assert keys[0] == keys[1]
