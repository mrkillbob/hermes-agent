"""Native, inert processes prove lossless per-run diagnostic retention."""
import json
from pathlib import Path
import subprocess

import pytest


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('mode', ['retention', 'failed-and-orphaned', 'stop-race'])
def test_runner_diagnostic_history_preserves_settled_output_and_bounds_completed_runs(tmp_path, mode):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-File',
         str(root / 'tests/scripts/fixtures/fleet_runner_diagnostics.ps1'),
         '-Helper', str(root / 'scripts/fleet_desktop_runtime.ps1'),
         '-Root', str(tmp_path), '-Mode', mode],
        capture_output=True, text=True, check=True, timeout=180,
    )
    assert json.loads(result.stdout) == {'mode': mode, 'passed': True}
