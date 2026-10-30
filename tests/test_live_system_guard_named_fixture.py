"""Native guard checks for the named fixture program invocation route."""
import subprocess
import sys

import pytest


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('launcher', ['Popen', 'run'])
def test_named_fixture_update_argument_is_blocked_before_launch(tmp_path, launcher):
    # If the guard regresses, this disposable program can only write the sentinel.
    program = tmp_path / 'hermes_checkout_lock_holder.py'
    sentinel = tmp_path / 'launched'
    program.write_text(
        "from pathlib import Path\nimport sys\n"
        "Path(sys.argv[2]).write_text('launched', encoding='utf-8')\n",
        encoding='utf-8',
    )
    command = [sys.executable, str(program), 'update', str(sentinel)]
    with pytest.raises(RuntimeError, match=r"live-system guard: blocked .*`hermes update`"):
        if launcher == 'Popen':
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
                proc.communicate(timeout=10)
        else:
            subprocess.run(command, capture_output=True, check=True, timeout=10)
    assert not sentinel.exists()
