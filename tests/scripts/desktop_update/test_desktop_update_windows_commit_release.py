"""Native Windows commit release contracts."""
from __future__ import annotations
from pathlib import Path
import subprocess
import sys
import time
import pytest
from tests.scripts.desktop_update.test_desktop_update_windows_commit_outcome import _R6_CHECKOUT_HOLDER
from tests.scripts.desktop_update.test_desktop_update_windows_commit_outcome import _handoff


@pytest.mark.platforms('windows')
def test_result_finished_at_is_stamped_after_the_r6_release_wait(tmp_path: Path) -> None:
    # The relaunched Desktop drops a non-manual result whose finished_at is 30 minutes old; the
    # R6 wait lasts up to 2 h. The result must carry the time the hand-off actually finished.
    holders = []
    ready = tmp_path / 'checkout-holder-ready'
    released = tmp_path / 'checkout-holder-released'

    def hold_checkout(home: Path, install: Path) -> None:
        proc = subprocess.Popen(
            [sys.executable, '-c', _R6_CHECKOUT_HOLDER, str(install / '.hermes-update.lock'),
             str(ready), str(home / 'logs/desktop-update-handoff.log'), str(released)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        holders.append(proc)
        deadline = time.monotonic() + 30
        while not ready.exists():
            assert time.monotonic() < deadline and proc.poll() is None, 'checkout holder never acquired the lock'
            time.sleep(0.05)
    try:
        code, out, _, result, home = _handoff(tmp_path, '-NoGateway', prepare=hold_checkout)
        holder_out, _ = holders[0].communicate(timeout=10)
        assert holders[0].returncode == 0, holder_out
    finally:
        for proc in holders:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
    assert code == 0, out
    log = (home / 'logs/desktop-update-handoff.log').read_text(encoding='utf-8-sig')
    assert 'keeping the update marker' in log, log
    actual_release = float(released.read_text(encoding='utf-8'))
    assert result['ok'] is True and result['finished_at'] >= int(actual_release) - 1, result


@pytest.mark.platforms('windows')
def test_marker_with_a_live_delegate_is_handed_to_it_at_finish(tmp_path: Path) -> None:
    # A7 release rule: the owner deletes its claim unless a delegate still runs; then that
    # delegate becomes the owner (canonical body, started_at kept, no delegate line).
    pid_file = tmp_path / 'delegate.pid'
    try:
        code, out, _, result, home = _handoff(tmp_path, HANDOFF_DELEGATE=str(pid_file))
        assert code == 0, out
        marker = (home / '.hermes-update-in-progress').read_bytes().decode().split('\n')
        assert marker[0] == pid_file.read_text(encoding='utf-8-sig'), marker
        assert marker[2].startswith('ct:') and marker[3:] == [''], marker
    finally:
        if pid_file.exists():
            subprocess.run(['taskkill', '/F', '/PID', pid_file.read_text(encoding='utf-8-sig')], capture_output=True)
