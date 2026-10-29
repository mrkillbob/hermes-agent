"""A refusal records its actual decision inputs without new process probes."""

import json
import logging
import time

import pytest

from hermes_cli import gateway_runtime, update_lock
from tests.hermes_cli.test_update_marker_corpus import CORPUS, _world


def _events(caplog):
    return [json.loads(r.message.split("gateway_update_fence=", 1)[1])
            for r in caplog.records if r.message.startswith("gateway_update_fence=")]


def test_live_owner_refusal_records_used_identity_without_probing_delegate(tmp_path, monkeypatch, caplog):
    calls = []

    def alive(pid):
        calls.append(("alive", pid))
        return True

    def birth(pid):
        calls.append(("birth", pid))
        return 10.0

    world = update_lock._World(999, 500.0, 1000.0, alive, birth)
    monkeypatch.setattr(update_lock, "_real_world", lambda: world)
    monkeypatch.setattr(update_lock, "checkout_lock_held",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected lock probe")))
    (tmp_path / update_lock.MARKER_NAME).write_text(
        "41\n900\nct:10.0\ndelegate:42 ct:20.0\nrun:private-sentinel\n")
    caplog.set_level(logging.INFO)

    assert gateway_runtime._update_fenced({tmp_path}) is True

    events = _events(caplog)
    assert len(events) == 1, "the refusal must preserve its decision evidence"
    event = events[0]
    assert event["reason"] == "marker_live"
    assert event["marker"]["owner"] == {
        "pid": 41, "recorded_birth": 10.0, "alive_probe": True,
        "birth_probe": "observed", "observed_birth": 10.0,
    }
    assert event["marker"]["delegate"]["alive_probe"] == "not_probed"
    assert event["checkout"]["result"] == "not_probed"
    assert calls == [("alive", 41), ("birth", 41)]
    assert "private-sentinel" not in json.dumps(event)
    assert str(tmp_path) not in json.dumps(event)


@pytest.mark.parametrize("case", CORPUS["judge"], ids=[case["name"] for case in CORPUS["judge"]])
def test_observed_marker_keeps_corpus_verdict_and_original_probe_sequence(case):
    traces = []
    for observed in (False, True):
        original = _world(case, "our_pid", "our_ct")
        calls = []

        def alive(pid):
            calls.append(("alive", pid))
            return original.alive(pid)

        def birth(pid):
            calls.append(("birth", pid))
            return original.ct_of(pid)

        world = update_lock._World(original.pid, original.ct, original.now, alive, birth)
        out = {}
        result = update_lock.judge_marker(case["text"].encode(), world,
                                          **({"observation": out} if observed else {}))
        assert dict(zip(("verdict", "owner", "run"), result)) == case["expect"]
        traces.append(calls)
    assert traces[0] == traces[1]


def test_fence_export_rebuilds_allowlisted_fields(caplog):
    from hermes_cli import update_lock_diagnostics as diagnostics
    caplog.set_level(logging.INFO)
    diagnostics.record_fence("marker_live", 0, marker={
        "verdict": "live", "selected_pid": 41, "raw": "private-sentinel",
        "owner": {"pid": 41, "recorded_birth": 10.0, "alive_probe": True,
                  "birth_probe": "observed", "observed_birth": 10.0, "argv": "private-sentinel"},
        "delegate": {}, "probes": [{"kind": "alive", "pid": 41, "result": True,
                                    "environment": "private-sentinel"}],
    }, checkout={"result": "not_probed", "path": "private-sentinel"})
    event = _events(caplog)[0]
    assert event["marker"]["owner"]["pid"] == 41
    assert "private-sentinel" not in json.dumps(event)


def test_broken_observer_and_logger_do_not_change_marker_acceptance(tmp_path, monkeypatch):
    from hermes_cli import update_lock_diagnostics as diagnostics
    world = update_lock._World(999, 500.0, 1000.0, lambda pid: True, lambda pid: 10.0)
    monkeypatch.setattr(update_lock, "_real_world", lambda: world)
    (tmp_path / update_lock.MARKER_NAME).write_text("41\n900\nct:10\n")

    def broken(*args, **kwargs):
        raise RuntimeError("private-sentinel")

    monkeypatch.setattr(diagnostics, "observe_marker", broken)
    monkeypatch.setattr(diagnostics.logger, "info", broken)
    assert gateway_runtime._update_fenced({tmp_path}) is True


def test_kernel_probe_error_preserves_unsealed_false_and_records_unknown_error(monkeypatch):
    monkeypatch.setattr(update_lock, "holds_checkout_lock", lambda *a: False)
    monkeypatch.setattr(update_lock, "_sealed_checkout_app", lambda *a: False)
    monkeypatch.setattr(update_lock, "checkout_lock_path", lambda *a: None)
    monkeypatch.setattr(update_lock, "_try_lock", lambda fd, **k: (_ for _ in ()).throw(OSError(5, "private-sentinel")))
    out = {}
    with monkeypatch.context() as patch:
        patch.setattr(update_lock.os, "open", lambda *a: 77)
        patch.setattr(update_lock.os, "close", lambda fd: None)
        assert update_lock.checkout_lock_held(observation=out) is False
    assert out == {"result": False, "reason": "probe_error", "error_type": "OSError", "errno": 5}


def test_delegate_with_unavailable_birth_is_not_reported_as_a_verified_identity(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(update_lock, "_real_world", lambda: update_lock._World(
        999, 500.0, 1000.0, lambda pid: pid == 42, lambda pid: None))
    (tmp_path / update_lock.MARKER_NAME).write_text("41\n900\nct:10\ndelegate:42 ct:20\n")
    caplog.set_level(logging.INFO)

    assert gateway_runtime._update_fenced({tmp_path}) is True

    marker = _events(caplog)[0]["marker"]
    assert marker["verdict"] == "live"  # Existing legacy-age fallback is preserved.
    assert marker["owner"]["alive_probe"] is False
    assert marker["delegate"]["alive_probe"] is True
    assert marker["delegate"]["birth_probe"] == "unavailable"
    assert marker["delegate"]["observed_birth"] is None


def test_checkout_open_error_is_the_actual_fail_closed_reason(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(update_lock, "_real_world", lambda: update_lock._World(
        999, 500.0, 1000.0, lambda pid: False, lambda pid: None))
    monkeypatch.setattr(update_lock, "holds_checkout_lock", lambda *a: False)
    monkeypatch.setattr(update_lock, "_sealed_checkout_app", lambda *a: False)
    (tmp_path / update_lock.MARKER_NAME).write_text("41\n900\nct:10\n")
    caplog.set_level(logging.INFO)

    def refused(*args, **kwargs):
        raise PermissionError(13, "private-sentinel")

    with monkeypatch.context() as patch:
        patch.setattr(update_lock.os, "open", refused)
        assert gateway_runtime._update_fenced({tmp_path}) is True

    event = _events(caplog)[0]
    assert event["reason"] == "checkout_held"
    assert event["checkout"] == {
        "result": True, "reason": "open_error", "error_type": "PermissionError", "errno": 13,
    }
    assert "private-sentinel" not in json.dumps(event)


def test_marker_read_error_never_adds_a_second_probe(tmp_path, monkeypatch, caplog):
    def refused(*args):
        raise PermissionError(13, "private-sentinel")

    monkeypatch.setattr(type(tmp_path), "read_bytes", refused)
    monkeypatch.setattr(update_lock, "judge_marker",
                        lambda *a, **k: pytest.fail("a failed read must not judge new bytes"))
    caplog.set_level(logging.INFO)

    assert gateway_runtime._update_fenced({tmp_path}) is True

    event = _events(caplog)[0]
    assert event["reason"] == "marker_read_error"
    assert event["error_type"] == "PermissionError"
    assert event["checkout"]["result"] == "not_probed"
    assert "private-sentinel" not in json.dumps(event)


def test_empty_marker_receipt_uses_the_age_that_actually_decided_grace(tmp_path, monkeypatch, caplog):
    from types import SimpleNamespace
    (tmp_path / update_lock.MARKER_NAME).write_bytes(b"")
    caplog.set_level(logging.INFO)
    with monkeypatch.context() as patch:
        patch.setattr(time, "time", lambda: 1000.0)
        patch.setattr(type(tmp_path), "stat", lambda *a, **k: SimpleNamespace(st_mtime=999.75))
        assert gateway_runtime._update_fenced({tmp_path}) is True
    event = _events(caplog)[0]
    assert event["reason"] == "empty_marker_in_flight"
    assert event["marker"]["empty_age_seconds"] == 0.25
    assert event["marker"]["empty_grace_seconds"] == update_lock.EMPTY_MARKER_GRACE_SECONDS


def test_empty_marker_stat_error_is_distinct_from_a_read_error(tmp_path, monkeypatch, caplog):
    (tmp_path / update_lock.MARKER_NAME).write_bytes(b"")
    caplog.set_level(logging.INFO)

    def denied(*args, **kwargs):
        raise PermissionError(13, "private-sentinel")

    with monkeypatch.context() as patch:
        patch.setattr(type(tmp_path), "stat", denied)
        assert gateway_runtime._update_fenced({tmp_path}) is True
    event = _events(caplog)[0]
    assert event["reason"] == "marker_stat_error"
    assert event["error_type"] == "PermissionError"


@pytest.mark.parametrize("failed_offset,expected_arm", [(100, "windows_primary"), (101, "windows_leases")])
def test_byte_range_lock_records_actual_arm_and_error_without_a_second_attempt(failed_offset, expected_arm):
    calls = []

    def locking(fd, mode, count):
        calls.append(("locking", fd, mode, count))
        if position[0] == failed_offset:
            error = OSError(22, "private-sentinel")
            error.winerror = 87
            raise error

    position = [0]

    def seek(fd, offset, whence):
        position[0] = offset
        calls.append(("seek", fd, offset))

    def byte_lock(fd, offset, count=1, *, observation=None):
        return update_lock._attempt_byte_lock(fd, offset, count, seek=seek, locking=locking,
                                               mode=7, observation=observation)

    out = {}
    result = update_lock._try_byte_lock(77, 100, 4, lock_bytes=byte_lock,
                                       unlock=lambda *a: calls.append(("unlock", *a)), observation=out)
    assert result is False
    rejected = out["attempts"][-1]
    assert rejected == {"arm": expected_arm, "result": False, "reason": "locking_error",
                        "error_type": "OSError", "errno": 22, "winerror": 87}
    assert sum(row[0] == "locking" for row in calls) == (1 if failed_offset == 100 else 2)
    assert ("unlock", 77) in calls if failed_offset == 101 else not any(row[0] == "unlock" for row in calls)
    assert "private-sentinel" not in json.dumps(out)


def test_byte_range_seek_error_keeps_original_exception_and_observed_arm():
    error = PermissionError(13, "private-sentinel")

    def denied(*args):
        raise error

    def byte_lock(fd, offset, count=1, *, observation=None):
        return update_lock._attempt_byte_lock(fd, offset, count, seek=denied,
                                               locking=lambda *a: pytest.fail("seek failed"), mode=7,
                                               observation=observation)

    out = {}
    with pytest.raises(PermissionError) as caught:
        update_lock._try_byte_lock(77, 100, 4, lock_bytes=byte_lock,
                                   unlock=lambda *a: pytest.fail("nothing acquired"), observation=out)
    assert caught.value is error
    assert out["attempts"] == [{"arm": "windows_primary", "result": "raised", "reason": "seek_error",
                                "error_type": "PermissionError", "errno": 13}]


def test_byte_range_success_retains_primary_and_releases_only_lease_probe():
    calls = []

    def lock(fd, offset, count=1, **kwargs):
        calls.append(("lock", fd, offset, count))
        return True

    assert update_lock._try_byte_lock(
        77, 100, 4, lock_bytes=lock, unlock=lambda *a: calls.append(("unlock", *a)), observation={}) is True
    assert calls == [("lock", 77, 100, 1), ("lock", 77, 101, 4), ("unlock", 77, 101, 4)]
