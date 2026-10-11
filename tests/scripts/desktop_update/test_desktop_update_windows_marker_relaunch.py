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
from tests.scripts.desktop_update.test_desktop_update_windows_marker import sleeper as sleeper  # noqa: PLC0414


@pytest.mark.platforms('windows')
def test_desktop_that_never_exits_is_not_relaunched_over(
    tmp_path: Path, sleeper: subprocess.Popen, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The verdict (refuse with 4, never relaunch) is the same at any ceiling; the production
    # 150 s one alone pushed this file to the runner's 300 s per-file cap on a loaded runner.
    monkeypatch.setenv('HERMES_UPDATE_DESKTOP_EXIT_SECONDS', '5')
    install = tmp_path / 'checkout'
    publish_fixture_launcher(install, CLI)
    home = tmp_path / 'home'; home.mkdir()
    # The Desktop's bridge claim, which a -DesktopPid hand-off adopts (A4).
    (home / MARKER).write_bytes(f'{sleeper.pid}\n{int(time.time())}\nct:{_creation_time(sleeper.pid)}\n'.encode())
    relaunch = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'hostname.exe'
    _, code, out = _run(home, '-DesktopPid', str(sleeper.pid), '-RelaunchExe', str(relaunch),
                        install=install)
    assert code == 4, out
    log = (home / 'logs/desktop-update-handoff.log').read_text(encoding='utf-8-sig')
    assert 'did not exit within 5s' in log, log   # the override reached the script's wait
    assert 'relaunching desktop' not in log, log
