"""Managed launchers protect venv identity before interpreter startup."""
from pathlib import Path
import subprocess
import sys

import pytest

from pm import lifetime


def _target(tmp_path):
    state = tmp_path / 'installs' / 'fixture'
    generation = state / 'test-environment' / 'gen-fixture'
    with lifetime.activity(state, create=True):
        binary = generation / 'venv' / ('Scripts' if sys.platform == 'win32' else 'bin') / Path(sys.executable).name
        binary.parent.mkdir(parents=True)
        (binary.parent.parent / 'pyvenv.cfg').write_text('home = ' + str(Path(sys.base_prefix) / 'bin'))
        (generation / '.lease-managed').touch()
    return state, generation, binary


@pytest.mark.platforms("posix")
def test_invocation_directory_preserves_venv_identity(tmp_path):
    from pm.lifetime_launch import target_generation

    _, generation, binary = _target(tmp_path)
    # The executable itself can point outside the venv; packages still belong to it.
    binary.symlink_to(sys.executable)
    assert target_generation(binary) == generation
    assert target_generation(tmp_path / 'external' / 'bin' / 'python') is None


def test_failed_spawn_releases_only_its_activity(tmp_path, monkeypatch):
    from pm.lifetime_launch import popen

    state, generation, binary = _target(tmp_path)
    def failed(*args, **kwargs):
        with lifetime.gate(state) as guard:
            assert guard.held(generation=generation)
        raise OSError('synthetic spawn failure')
    monkeypatch.setattr(subprocess, 'Popen', failed)
    with pytest.raises(OSError, match='synthetic spawn failure'):
        popen([str(binary), '-c', 'pass'])
    with lifetime.gate(state) as guard:
        assert not guard.held()


def test_removed_generation_is_refused_under_registration_gate(tmp_path):
    from pm.lifetime_launch import popen
    import shutil

    _, generation, binary = _target(tmp_path)
    shutil.rmtree(generation)
    with pytest.raises(lifetime.LifetimeUnavailable):
        popen([str(binary), '-c', 'pass'])
