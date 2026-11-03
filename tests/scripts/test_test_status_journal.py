"""Synthetic subprocess seams only: no OS test dispatch or real child signals."""
import io
import json
import sys
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from scripts import run_tests_parallel as runner
from scripts import test_status_journal as journal


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def case(tmp_path, monkeypatch):
    path = tmp_path / 'status.jsonl'
    sink = journal.StatusJournal(path)
    monkeypatch.setattr(runner, '_STATUS_JOURNAL', sink)
    monkeypatch.setattr(runner, '_runner_scratch_root', lambda: tmp_path)
    kill, detached, cleanup = Mock(), Mock(), Mock()
    monkeypatch.setattr(runner, '_kill_tree', kill)
    monkeypatch.setattr(runner, '_kill_detached_leftovers', detached)
    monkeypatch.setattr(runner, '_rmtree_force', cleanup)
    proc = Mock(returncode=1)
    proc.communicate.return_value = ('SECRET fixture output\n1 failed', None)
    monkeypatch.setattr('scripts.runner_lifetime.spawn_pytest', lambda *a, **kw: (proc, 123))
    file = tmp_path / 'tests' / 'test_synthetic.py'
    yield path, file, proc, kill, detached, cleanup
    sink.close()


def test_failed_result_is_durable_before_cleanup_and_body_free(case):
    path, file, _proc, kill, detached, cleanup = case
    def inspect_cleanup(*args):
        events = rows(path)
        assert [e['phase'] for e in events] == ['started', 'output_collected', 'scratch_cleanup_started']
        assert events[1]['observed_exit_code'] == 1
        assert 'SECRET' not in path.read_text()
    cleanup.side_effect = inspect_cleanup
    result = runner._run_one_file_once(file, [], file.parents[1], 3)
    assert result[1] == 1
    assert result[3]['failed'] == 1
    kill.assert_called_once()
    detached.assert_called_once()
    assert rows(path)[-1]['phase'] == 'scratch_cleanup_complete'


def test_cleanup_failure_keeps_prior_checkpoint(case):
    path, file, _proc, _kill, _detached, cleanup = case
    cleanup.side_effect = RuntimeError('synthetic teardown failure')
    with pytest.raises(RuntimeError, match='teardown'):
        runner._run_one_file_once(file, [], file.parents[1], 3)
    assert rows(path)[-1]['phase'] == 'scratch_cleanup_started'
    assert rows(path)[1]['observed_exit_code'] == 1
    assert 'teardown failure' not in path.read_text()


def test_timeout_does_not_confuse_direct_child_exit_with_output_completion(case):
    path, file, proc, kill, detached, cleanup = case
    proc.returncode = 0  # e.g. stdout retained by a descendant; cause is not inferred.
    proc.communicate.side_effect = [subprocess.TimeoutExpired('secret command', 3), ('', None)]
    result = runner._run_one_file_once(file, [], file.parents[1], 3)
    assert result[1] == 124
    event = rows(path)[1]
    assert event['phase'] == 'timed_out'
    assert event['observed_exit_code'] == 0 and event['runner_exit_code'] == 124
    assert 'secret command' not in path.read_text()
    kill.assert_called_once()
    detached.assert_called_once()
    cleanup.assert_called_once()


def test_interruption_still_runs_owned_cleanup(case):
    path, file, proc, kill, detached, cleanup = case
    proc.communicate.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        runner._run_one_file_once(file, [], file.parents[1], 3)
    assert rows(path)[1]['phase'] == 'interrupted'
    kill.assert_called_once()
    detached.assert_called_once()
    cleanup.assert_called_once()


@pytest.mark.parametrize("closed_stdout", [False, True])
def test_journal_write_failure_does_not_change_test_result_or_cleanup(case, monkeypatch, closed_stdout):
    path, file, _proc, kill, detached, cleanup = case
    if closed_stdout:
        stream = io.StringIO()
        stream.close()
        monkeypatch.setattr(sys, "stdout", stream)
    def unavailable(*args):
        raise OSError('sensitive device detail')
    monkeypatch.setattr(journal.os, 'write', unavailable)
    result = runner._run_one_file_once(file, [], file.parents[1], 3)
    assert result[1] == 1
    kill.assert_called_once()
    detached.assert_called_once()
    cleanup.assert_called_once()
    assert path.read_bytes() == b''


def test_exclusive_destination_preserves_existing_data(tmp_path):
    path = tmp_path / 'existing'
    path.write_text('preserve')
    with pytest.raises(FileExistsError):
        journal.StatusJournal(path)
    assert path.read_text() == 'preserve'


def test_concurrent_attempts_are_bounded_body_free_and_independent(tmp_path, monkeypatch):
    path = tmp_path / 'status'
    sink = journal.StatusJournal(path)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            attempts = list(pool.map(lambda i: sink.start(tmp_path / 'tests' / f'test_{i}.py', tmp_path), range(20)))
        assert len({a for a, _ in attempts}) == 20
        assert len(rows(path)) == 20
        monkeypatch.setattr(journal, '_MAX_RECORDS', 20)
        sink.start(tmp_path / 'outside-secret.py', tmp_path)
        assert len(rows(path)) == 20
        assert set(rows(path)[0]) == {'schema','attempt','file','phase','elapsed_ms','observed_exit_code','runner_exit_code'}
    finally:
        sink.close()


@pytest.mark.parametrize('file', ['/secret/test.py', 'tests/../secret.py', 'tests/test.py::SECRET'])
def test_rejects_untrusted_path_payload(tmp_path, file):
    path = tmp_path / 'status'
    sink = journal.StatusJournal(path)
    try:
        sink.emit(1, file, 'started', 0)
        assert path.read_bytes() == b''
    finally:
        sink.close()


def test_external_path_is_not_disclosed(tmp_path):
    path = tmp_path / 'status'
    sink = journal.StatusJournal(path)
    try:
        sink.start(tmp_path.parent / 'secret.py', tmp_path)
        assert rows(path)[0]['file'] is None
    finally:
        sink.close()


@pytest.mark.parametrize("value, expected", [(None, (None, 1)), ("2/3", (2, 3))])
def test_slice_consumption_preserves_nested_runner_behavior(monkeypatch, value, expected):
    monkeypatch.delenv("HERMES_TEST_SLICE", raising=False)
    assert runner._parse_slice(value) == expected
    monkeypatch.setenv("HERMES_TEST_SLICE", "1/4")
    assert runner._parse_slice(None) == (1, 4)
    assert "HERMES_TEST_SLICE" not in runner.os.environ


def test_invalid_slice_fails_before_discovery():
    with pytest.raises(SystemExit) as exc:
        runner._parse_slice("bad")
    assert exc.value.code == 2
