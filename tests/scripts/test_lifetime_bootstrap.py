"""A custom-location bootstrap remains available after the runner drops its environment."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.scripts.test_run_tests_parallel import _probe_root


@pytest.mark.platforms('posix')
def test_custom_runtime_bootstrap_survives_hermetic_launch(tmp_path):
    from pm import lifetime

    repo = _probe_root(tmp_path)
    import shutil
    original = Path(__file__).resolve().parents[2] / 'scripts'
    for name in ('run_tests.sh', '_activation.sh'):
        shutil.copy2(original / name, repo / 'scripts')
    tools = tmp_path / 'custom-tools'
    bootstrap = tools / 'python-fixture' / 'bin' / 'python3'
    bootstrap.parent.mkdir(parents=True)
    bootstrap.symlink_to(sys.executable)
    state = tmp_path / 'custom-home' / 'installs' / 'fixture'
    with lifetime.activity(state, create=True):
        target = state / 'test-environment' / 'gen-fixture' / 'venv' / 'bin' / 'python'
        target.parent.mkdir(parents=True)
        # Fixture target delegates to the existing test interpreter, without installing anything.
        target.write_text('#!/bin/sh\nexec ' + __import__('shlex').quote(sys.executable) + ' "$@"\n')
        target.chmod(0o700)
    probe = tmp_path / 'test_probe.py'
    probe.write_text('def test_probe():\n    assert True\n')
    fake_home = tmp_path / 'home'
    fake_home.mkdir()
    result = subprocess.run(['bash', str(repo / 'scripts' / 'run_tests.sh'), '-j', '1', str(probe), '-q'],
                            cwd=repo, capture_output=True, text=True, timeout=60, check=False,
                            env={**os.environ, 'HOME': str(fake_home), 'HERMES_HOME': str(fake_home / 'hermes'),
                                 'HERMES_RUNTIME_DIR': str(tools), 'HERMES_PYTHON': str(target)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 tests passed' in result.stdout
    with lifetime.gate(state) as guard:
        assert not guard.held()
