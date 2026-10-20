"""Offline negative contracts for proposed handoff; no runtime or engine calls."""

from types import SimpleNamespace

import pytest

from hermes_cli.local_runtime.request_control import ChildState, ScopedRequest










def idle_probe():
    return SimpleNamespace(
        model_id="target-model", read=lambda **kw: ChildState(True, (), 0.0, 0.0)
    )


def test_cancel_before_start_cannot_be_confirmed_cancelled():
    request = ScopedRequest(idle_probe(), {"model": "target-model"})
    assert not request.cancel(timeout_s=0, settle_reads=1, interval_s=0).cancelled


def test_request_body_model_must_match_probed_child():
    with pytest.raises(ValueError):
        ScopedRequest(idle_probe(), {"model": "foreign-model"})


def test_expired_verification_budget_does_not_initiate_probe():
    reads = []
    probe = idle_probe()
    probe.read = lambda **kw: reads.append(True) or ChildState(True, (), 0.0, 0.0)
    ScopedRequest(probe, {"model": "target-model"})._verify(0, 2, 0, 0)
    assert reads == []


def test_evict_is_not_available_before_cancel_or_explicit_authority():
    unloads = []
    probe = idle_probe()
    probe.unload = lambda: unloads.append(True)
    probe.model_status = lambda: "unloaded"
    result = ScopedRequest(probe, {"model": "target-model"}).evict(
        timeout_s=1, interval_s=0
    )
    assert unloads == []
    assert not result.cancelled


def test_full_response_during_idle_verification_is_completion_not_cancel():
    import time
    from hermes_cli.local_runtime.request_control import CancelOutcome

    probe = idle_probe()
    request = ScopedRequest(probe, {"model": "target-model"})

    def completed_read(**kw):
        request._response = (200, b"complete")
        request._finished.set()
        return ChildState(True, (), 0.0, 0.0)

    probe.read = completed_read
    result = request._verify(1, 1, 0, time.monotonic())
    assert result.outcome is CancelOutcome.COMPLETED_BEFORE_CANCEL


def test_malformed_slot_boolean_is_not_idle(monkeypatch):
    from hermes_cli.local_runtime.request_control import ManagedChildProbe, ProbeError

    probe = ManagedChildProbe("http://127.0.0.1:12345", "test", "target-model")
    monkeypatch.setattr(probe, "model_status", lambda **kw: "loaded")
    monkeypatch.setattr(
        probe, "_json", lambda route, **kw: [{"id": 0, "is_processing": None}]
    )
    monkeypatch.setattr(
        probe,
        "_get",
        lambda route, **kw: (
            200,
            b"llamacpp:requests_processing 0\nllamacpp:requests_deferred 0\n",
        ),
    )
    with pytest.raises(ProbeError):
        probe.read()




def test_cancel_before_start_prevents_later_dispatch():
    from hermes_cli.local_runtime.request_control import RequestRefused

    request = ScopedRequest(idle_probe(), {"model": "target-model"})
    request.cancel(timeout_s=0)
    with pytest.raises(RequestRefused):
        request.start()










