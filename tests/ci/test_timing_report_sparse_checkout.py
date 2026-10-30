"""The reporter must start using only the files its CI checkout materializes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
OFFLINE_HELP = """
import runpy, sys
def no_network(event, args):
    if event.startswith('socket.') or event in ('urllib.Request', 'http.client.connect'):
        raise AssertionError('timing sparse fixture must stay offline')
sys.addaudithook(no_network)
sys.argv = [sys.argv[1], '--help']
runpy.run_path(sys.argv[0], run_name='__main__')
"""


def test_declared_timing_sparse_checkout_has_its_import_closure(tmp_path):
    workflow = (ROOT / '.github/workflows/ci.yaml').read_text()
    _, marker, section = workflow.partition('  ci-timings:\n')
    assert marker
    lines = section.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith('          sparse-checkout:'))
    value = lines[index].split(':', 1)[1].strip()
    if value == '|':
        paths = []
        for line in lines[index + 1:]:
            if not line.startswith('            '):
                break
            if line.strip():
                paths.append(line.strip())
    else:
        paths = [value]
    assert paths
    for value in paths:
        relative = Path(value)
        assert not relative.is_absolute() and '..' not in relative.parts
        source, destination = ROOT / relative, tmp_path / relative
        assert source.is_file(), value
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    reporter = tmp_path / 'scripts/ci/timings_report.py'
    done = subprocess.run(
        [sys.executable, '-I', '-S', '-B', '-c', OFFLINE_HELP, str(reporter)],
        cwd=tmp_path, env={'HOME': str(tmp_path), 'PATH': os.defpath, 'HERMES_HOME': str(tmp_path / 'home')},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert 'usage:' in done.stdout and '--baseline' in done.stdout
