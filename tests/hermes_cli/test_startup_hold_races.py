"""Actual process reservation and lexical-profile recovery ordering contracts."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.platforms('any')
def test_actual_standalone_reservation_serializes_hold_publication(tmp_path, monkeypatch):
    from gateway import drain_startup
    from pm.lifetime_launch import popen
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(drain_startup, '_refuse_legacy_writers', lambda *args: None)
    monkeypatch.setattr(drain_startup, '_checkout_root', lambda: Path(os.environ['HERMES_HOME']))
    (tmp_path / 'ESTOP').touch()
    code = ('from hermes_cli.startup_hold import writer_scope\n'
            'import sys\n'
            'with writer_scope():\n'
            '    print("reserved", flush=True)\n'
            '    sys.stdin.readline()\n')
    root = Path(__file__).resolve().parents[2]
    with popen([sys.executable, '-c', code], cwd=root, env={**os.environ, 'HERMES_HOME': str(tmp_path)},
               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        assert child.stdout.readline().strip() == 'reserved'
        with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
            drain_startup.begin()
        assert not (tmp_path / '.drain_request.json').exists()
        out, err = child.communicate('\n', timeout=10)
        assert child.returncode == 0, (out, err)
    value = drain_startup.begin()
    with popen([sys.executable, '-c', code], cwd=root, env={**os.environ, 'HERMES_HOME': str(tmp_path)},
               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        out, err = child.communicate('\n', timeout=10)
    assert child.returncode != 0 and not out
    assert 'startup_hold_writers_busy' in err
    drain_startup.restore(hold_id=value['hold_id'])


@pytest.mark.platforms('posix')
def test_lexical_custom_profile_hold_precedes_torn_tree_recovery(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    shutil.copy2(Path(__file__).resolve().parents[2] / 'hermes_bootstrap.py', source / 'hermes_bootstrap.py')
    package = source / 'hermes_cli'
    package.mkdir()
    (package / '__init__.py').touch()
    witness = tmp_path / 'recovery-entered'
    (package / '_early_recovery.py').write_text('from pathlib import Path\n'
        'def restore_interrupted_pull(*args):\n' + f'    Path({str(witness)!r}).touch()\n    return False\n')
    home = tmp_path / 'custom'
    profiles = home / 'profiles'
    profiles.mkdir(parents=True)
    actual = tmp_path / 'actual-profile'
    actual.mkdir()
    (profiles / 'work').symlink_to(actual, target_is_directory=True)
    (home / '.drain_request.json').write_text('{"startup_hold":{}}')
    result = subprocess.run([sys.executable, '-S', '-c', 'import hermes_bootstrap'], cwd=source,
        env={**os.environ, 'PYTHONPATH': str(source), 'HERMES_HOME': str(profiles / 'work')},
        capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode != 0
    assert 'startup_hold' in result.stderr
    assert not witness.exists()
