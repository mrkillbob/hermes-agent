"""Offline negative contracts for proposed handoff; no runtime or engine calls."""

from types import SimpleNamespace

import pytest

from hermes_cli.local_runtime import memory_accounting as ma
from hermes_cli.local_runtime.request_control import ChildState, ScopedRequest


def test_prompt_cache_zero_does_not_establish_total_growth_ceiling():
    assert (
        ma.additional_memory_bound(["s", "--cache-ram", "0"], {}, None).limit_bytes
        is None
    )


def test_sampled_nonexceedance_does_not_prove_true_peak_within_limit():
    assert (
        ma.MemoryMeasurement(100, 110, 105, 2, 0).within(
            ma.MemoryBound(100, "test", "test")
        )
        is None
    )


def test_explicit_foreign_alias_never_wins_sole_child_fallback():
    child = SimpleNamespace(
        cmdline=lambda: ["llama-server", "--alias", "foreign-model"]
    )
    router = SimpleNamespace(children=lambda recursive: [child])
    assert ma.model_child(router, "target-model", ["target-model"]) is None


def test_changed_process_coverage_invalidates_additional_measurement():
    sets = iter(([1], [2]))
    with ma.MemorySampler(
        lambda: next(sets, [2]),
        reader=lambda pid: 100,
        interval_s=10,
        identity_reader=lambda pid: 1.0,
    ) as sampler:
        pass
    assert not sampler.result.complete


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


def test_duplicate_process_ids_invalidate_measurement():
    with ma.MemorySampler(
        lambda: [1, 1],
        reader=lambda pid: 100,
        interval_s=10,
        identity_reader=lambda pid: 1.0,
    ) as sampler:
        pass
    assert not sampler.result.complete


def test_cancel_before_start_prevents_later_dispatch():
    from hermes_cli.local_runtime.request_control import RequestRefused

    request = ScopedRequest(idle_probe(), {"model": "target-model"})
    request.cancel(timeout_s=0)
    with pytest.raises(RequestRefused):
        request.start()


def test_same_pid_birth_change_invalidates_measurement():
    births = iter((10.0, 10.0, 11.0, 11.0))
    with ma.MemorySampler(
        lambda: [1],
        reader=lambda pid: 100,
        interval_s=10,
        identity_reader=lambda pid: next(births),
    ) as sampler:
        pass
    assert not sampler.result.complete


def test_birth_change_during_sample_invalidates_measurement():
    births = iter((10.0, 11.0, 11.0, 11.0))
    with ma.MemorySampler(
        lambda: [1],
        reader=lambda pid: 100,
        interval_s=10,
        identity_reader=lambda pid: next(births),
    ) as sampler:
        pass
    assert not sampler.result.complete


def test_memory_total_bound_does_not_execute_engine(monkeypatch):
    def forbidden(executable):
        raise AssertionError("engine execution")

    monkeypatch.setattr(ma, "_engine_default_cache_ram_mib", forbidden)
    assert not ma.additional_memory_bound(["s"], {}, "/never-run").available
    assert not ma.child_bound(object()).available


def test_explicit_foreign_alias_overrides_matching_model_filename():
    child = SimpleNamespace(
        cmdline=lambda: ["s", "--alias", "foreign", "--model", "/models/target.gguf"]
    )
    router = SimpleNamespace(children=lambda recursive: [child])
    assert ma.model_child(router, "target", ["target"]) is None


def test_duplicate_alias_flags_are_not_a_unique_child_identity():
    child = SimpleNamespace(
        cmdline=lambda: ["s", "--alias", "foreign", "--alias", "target"]
    )
    router = SimpleNamespace(children=lambda recursive: [child])
    assert ma.model_child(router, "target", ["target"]) is None
