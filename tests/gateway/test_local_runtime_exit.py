"""A timed-out native cleanup must not be joined again by interpreter exit."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.platforms('linux', 'macos', 'windows')
def test_interpreter_exit_does_not_reenter_a_timed_out_runtime_stop():
    fixture = Path(__file__).parent / 'fixtures' / 'local_runtime_exit.py'
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    proc = subprocess.Popen([sys.executable, str(fixture)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()  # Only the inert helper created by this test.
        output, _ = proc.communicate(timeout=10)
    assert 'gateway stop returned' in output, output
    assert not timed_out, 'atexit re-entered the timed-out native cleanup'
    assert proc.returncode == 0, output
