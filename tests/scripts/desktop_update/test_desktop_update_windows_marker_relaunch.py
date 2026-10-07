"""Native Windows marker relaunch contracts."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import time
import pytest
from tests.installation_launcher_fixture import publish_fixture_launcher
from tests.scripts.desktop_update.test_desktop_update_windows_marker import CLI
from tests.scripts.desktop_update.test_desktop_update_windows_marker import MARKER
from tests.scripts.desktop_update.test_desktop_update_windows_marker import _creation_time
from tests.scripts.desktop_update.test_desktop_update_windows_marker import _run
from tests.scripts.desktop_update.test_desktop_update_windows_marker import sleeper as sleeper


@pytest.mark.platforms('windows')
def test_desktop_that_never_exits_is_not_relaunched_over(
    tmp_path: Path, sleeper: subprocess.Popen,
) -> None:
    install = tmp_path / 'checkout'
    publish_fixture_launcher(install, CLI)
    home = tmp_path / 'home'; home.mkdir()
    # The Desktop's bridge claim, which a -DesktopPid hand-off adopts (A4).
    (home / MARKER).write_bytes(f'{sleeper.pid}\n{int(time.time())}\nct:{_creation_time(sleeper.pid)}\n'.encode())
    relaunch = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'hostname.exe'
    _, code, out = _run(home, '-DesktopPid', str(sleeper.pid), '-RelaunchExe', str(relaunch),
                        install=install, timeout=180)
    assert code == 4, out
    log = (home / 'logs/desktop-update-handoff.log').read_text(encoding='utf-8-sig')
    assert 'relaunching desktop' not in log, log
