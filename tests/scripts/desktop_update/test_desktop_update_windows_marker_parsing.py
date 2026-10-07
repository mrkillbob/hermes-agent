"""Native Windows marker parsing contracts."""
from __future__ import annotations
from pathlib import Path
import subprocess
import time
import pytest
from tests.scripts.desktop_update.test_desktop_update_windows_marker import MARKER
from tests.scripts.desktop_update.test_desktop_update_windows_marker import _BODIES
from tests.scripts.desktop_update.test_desktop_update_windows_marker import _creation_time
from tests.scripts.desktop_update.test_desktop_update_windows_marker import _run
from tests.scripts.desktop_update.test_desktop_update_windows_marker import sleeper as sleeper


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('case', sorted(_BODIES))
def test_marker_bodies_are_parsed_positionally_like_every_other_reader(
    tmp_path: Path, sleeper: subprocess.Popen, case: str,
) -> None:
    template, verdict = _BODIES[case]
    now = int(time.time())
    body = template.format(pid=sleeper.pid, now=now, old=now - 1300, ct=_creation_time(sleeper.pid)).encode()
    (tmp_path / MARKER).write_bytes(body)
    pid, code, out = _run(tmp_path, '-SelfTestMarker', '-NoMarkerCleanup')
    if verdict == 'live':
        assert code == 2, out
        assert (tmp_path / MARKER).read_bytes() == body
    else:
        assert code == 0, out
        assert (tmp_path / MARKER).read_bytes().decode().split('\n')[0] == str(pid)
