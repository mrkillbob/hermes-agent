"""Timeout receipts retain useful npm state without publishing private logs."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from tests.e2e.core.windows_update import _timeout_evidence as evidence


class Process:
    def is_running(self):
        return True

    def children(self):
        return []

    def cpu_times(self):
        return SimpleNamespace(user=2.5, system=1.0)

    def memory_info(self):
        return SimpleNamespace(rss=4096)


def test_timeout_receipt_sanitizes_private_npm_logs_and_retains_resource_observations(tmp_path):
    command_temp = tmp_path / 'command'
    logs = command_temp / 'hermes-npm-logs-owned'
    logs.mkdir(parents=True)
    (logs / 'debug.log').write_text(
        '1 silly fetch manifest private-package@https://secret.invalid/?token=SECRET\n'
        '2 http fetch GET 200 https://secret.invalid/SECRET 200ms (cache miss)\n'
        '3 info run private-package@1.0.0 install node_modules/private SECRET\n'
        '4 error code ETIMEDOUT\n5 error code SECRET\n'
    )
    output = tmp_path / 'timeout.json'
    evidence.capture_timeout(output, command_temp, Process())
    raw = output.read_text()
    detail = json.loads(raw)
    assert 'SECRET' not in raw and 'private' not in raw and 'https' not in raw
    assert detail['npm']['logs'][0]['last_event'] == 'lifecycle_start'
    assert detail['npm']['logs'][0]['events'] == {
        'fetch_manifest': 1, 'http_fetch': 1, 'lifecycle_start': 1,
    }
    assert detail['npm']['logs'][0]['error_codes'] == ['ETIMEDOUT', 'unknown']
    assert detail['processes']['sampled'] == 1
    assert detail['processes']['cpu_seconds'] == 3.5
    assert detail['processes']['rss_bytes'] == 4096
    assert detail['host']['available_memory_bytes'] >= 0
    assert detail['host']['free_disk_bytes'] >= 0


def test_absent_truncated_and_unreadable_logs_remain_explicit(tmp_path, monkeypatch):
    output = tmp_path / 'timeout.json'
    evidence.capture_timeout(output, tmp_path / 'missing', None)
    assert json.loads(output.read_text())['npm']['state'] == 'unavailable'
    logs = tmp_path / 'hermes-npm-logs-owned'
    logs.mkdir()
    (logs / 'large.log').write_text('SECRET' * 20000 + '\n1 silly fetch manifest SECRET\n')
    evidence.capture_timeout(output, tmp_path, None)
    detail = json.loads(output.read_text())
    assert detail['npm']['logs'][0]['truncated'] is True
    assert detail['npm']['logs'][0]['last_event'] == 'fetch_manifest'
    assert detail['processes']['state'] == 'unavailable'
    original = evidence.os.open

    def denied(path, *args, **kwargs):
        if str(path).endswith('.log'):
            raise PermissionError('SECRET')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(evidence.os, 'open', denied)
    evidence.capture_timeout(output, tmp_path, None)
    assert json.loads(output.read_text())['npm']['logs'][0]['state'] == 'unreadable'
    assert 'SECRET' not in output.read_text()


def test_diagnostics_are_bounded_and_never_replace_timeout_cleanup(tmp_path, monkeypatch):
    for index in range(20):
        logs = tmp_path / f'hermes-npm-logs-{index}'
        logs.mkdir()
        (logs / 'debug.log').write_text('1 info run secret install node_modules/secret secret\n')
    output = tmp_path / 'timeout.json'
    evidence.capture_timeout(output, tmp_path, None)
    result = json.loads(output.read_text())['npm']
    assert result['truncated'] is True
    assert len(result['logs']) <= 16
    monkeypatch.setattr(Path, 'write_text', lambda *a, **k: (_ for _ in ()).throw(OSError('SECRET')))
    evidence.capture_timeout(output, tmp_path, None)  # Cleanup still runs if evidence cannot be saved.


@pytest.mark.platforms('linux', 'macos')
def test_symlinked_npm_directory_and_file_are_not_followed(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.log').write_text('1 error code ETIMEDOUT\n')
    owned = tmp_path / 'command'
    owned.mkdir()
    (owned / 'hermes-npm-logs-link').symlink_to(outside, target_is_directory=True)
    logs = owned / 'hermes-npm-logs-real'
    logs.mkdir()
    (logs / 'link.log').symlink_to(outside / 'secret.log')
    output = tmp_path / 'timeout.json'
    evidence.capture_timeout(output, owned, None)
    detail = json.loads(output.read_text())
    assert all(row['state'] == 'unreadable' for row in detail['npm']['logs'])
    assert 'ETIMEDOUT' not in output.read_text()


def test_departed_owner_is_not_replaced_by_unrelated_pid(tmp_path):
    class Departed(Process):
        def is_running(self):
            return False

        def children(self):
            raise AssertionError('must not enumerate a departed owner')

    output = tmp_path / 'timeout.json'
    evidence.capture_timeout(output, tmp_path, Departed())
    assert json.loads(output.read_text())['processes']['state'] == 'owner_departed'


def test_stalled_observation_returns_without_waiting_and_never_publishes_late(tmp_path, monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def stalled(*args):
        entered.set()
        assert release.wait(10)
        finished.set()
        return {'late': 'must not replace receipt'}

    monkeypatch.setattr(evidence, '_collect', stalled)
    output = tmp_path / 'timeout.json'
    try:
        evidence.capture_timeout(output, tmp_path, None, budget=0.05)
        assert entered.wait(5)
        assert not release.is_set()  # Capture returned while the observation is blocked.
        retained = output.read_bytes()
        assert json.loads(retained)['collection_timed_out'] is True
    finally:
        release.set()
    assert finished.wait(5)
    assert output.read_bytes() == retained
