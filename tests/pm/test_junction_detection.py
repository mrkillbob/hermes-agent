"""Windows junction admission must handle a fresh plugin destination (#222)."""
import os
from pathlib import Path
import subprocess

import pytest

from pm.filesystem import is_junction


@pytest.mark.platforms("windows")
def test_missing_destination_is_not_a_junction(tmp_path):
    assert not is_junction(tmp_path / "plugins" / "new-plugin")
    assert not is_junction(tmp_path)


@pytest.mark.platforms("windows")
def test_junction_detection_remains_opaque_and_permission_errors_propagate(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "plugin"
    command = str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe")
    result = subprocess.run(
        [command, "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        assert is_junction(junction)
        target.rmdir()
        assert is_junction(junction)
    finally:
        junction.rmdir()

    def unreadable(self):
        raise PermissionError("destination is unreadable")

    monkeypatch.setattr(Path, "lstat", unreadable)
    with pytest.raises(PermissionError):
        is_junction(tmp_path / "unreadable")
