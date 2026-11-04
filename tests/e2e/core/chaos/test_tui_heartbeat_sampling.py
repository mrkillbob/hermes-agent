"""Heartbeat counts describe the monitored window, never cleanup replies."""

from types import SimpleNamespace

import pytest

from tests.e2e.core.chaos._heartbeat_sampling import (
    HeartbeatSample, ObservationWindow, heartbeat_statistics, sampling_evidence,
)


@pytest.mark.parametrize(
    ("duration", "samples", "answered", "required", "enough"),
    [
        pytest.param(
            2.032734955,
            [(0.0, 1.603942719), (1.604, 1.611249129),
             (1.754, 1.755991229), (1.904, 1.920068575)],
            4, 3, True, id="observed-slow-valid-first-reply",
        ),
        pytest.param(2.0, [(0, .01), (.15, .16), (.30, .31), (.45, .46)],
                     4, 5, False, id="four-fast-replies-leave-a-gap"),
        pytest.param(10.0, [], 0, 5, False, id="no-sampling"),
        pytest.param(2.0, [(0, 2.9)], 0, 1, False, id="only-reply-after-cutoff"),
        pytest.param(2.0, [(0, None)], 0, 1, False, id="unanswered-at-cutoff"),
        pytest.param(2.0, [(0, .01), (.15, .16), (.30, .31), (.45, .46), (.60, .61)],
                     5, 5, True, id="five-normal-replies"),
    ],
)
def test_sampling_counts_only_answers_within_observation(duration, samples, answered, required, enough):
    evidence = sampling_evidence(
        tuple(HeartbeatSample(start, end) for start, end in samples),
        ObservationWindow(0.0, duration), interval=.15, minimum=5,
    )
    assert evidence.answered == answered
    assert evidence.required == required
    assert (evidence.answered >= evidence.required) is enough


@pytest.mark.parametrize("finished", [2.9, 20.0, None])
def test_post_cutoff_join_does_not_change_sampling_verdict(finished):
    evidence = sampling_evidence(
        (HeartbeatSample(0, finished),),
        ObservationWindow(0, 2), interval=.15, minimum=5,
    )
    assert evidence.answered == 0
    assert evidence.required == 1


def test_pre_window_replies_do_not_count_as_fault_samples():
    samples = tuple(HeartbeatSample(i * .15, i * .15 + .01) for i in range(5))
    evidence = sampling_evidence(samples, ObservationWindow(1, 3), interval=.15, minimum=5)
    assert evidence.answered == 0
    assert evidence.required == 5


@pytest.mark.parametrize(
    "samples",
    [
        (HeartbeatSample(0, 1), HeartbeatSample(.5, 1.5)),
        (HeartbeatSample(0, None), HeartbeatSample(1, 2)),
        (HeartbeatSample(1, .5),),
    ],
)
def test_non_serial_or_reversed_samples_are_rejected(samples):
    with pytest.raises(ValueError, match="serial"):
        sampling_evidence(samples, ObservationWindow(0, 3), interval=.15, minimum=5)


@pytest.mark.parametrize(
    ("latencies", "failures", "message"),
    [
        ((3.0,), (), "dispatcher blocked"),
        ((3.1,), (), "dispatcher blocked"),
        ((.01,), ("RPC refused",), "heartbeat RPC failed"),
    ],
)
def test_sampling_does_not_relax_latency_or_rpc_failure_guards(latencies, failures, message):
    with pytest.raises(AssertionError, match=message):
        heartbeat_statistics(
            scenario="synthetic", samples=(), window=ObservationWindow(0, 2),
            latencies=latencies, failures=failures, interval=.15, minimum=5, maximum_latency=3,
        )


def test_reply_during_cleanup_cannot_make_the_observation_healthy():
    with pytest.raises(AssertionError, match="inside a 2.0s observation window"):
        heartbeat_statistics(
            scenario="synthetic", samples=(HeartbeatSample(0, 2.9),),
            window=ObservationWindow(0, 2), latencies=(2.9,), failures=(),
            interval=.15, minimum=5, maximum_latency=3,
        )


@pytest.mark.platforms("linux")
def test_delayed_sampler_bookkeeping_is_not_rpc_occupancy(monkeypatch):
    from tests.e2e.core.chaos import _tui_rpc as rpc

    ticks = iter((0.0, 1.6))
    monkeypatch.setattr(rpc, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    gateway = SimpleNamespace(call_timing=lambda *args, **kwargs: (0.0, .001))
    heartbeat = rpc.Heartbeat(gateway, [("ping", {})])
    monkeypatch.setattr(heartbeat._stop, "wait", lambda _delay: heartbeat._stop.set())
    heartbeat._run()
    assert heartbeat.latencies == [.001]
    assert heartbeat._samples == [HeartbeatSample(0, .001)]
    evidence = sampling_evidence(
        (*heartbeat._samples, HeartbeatSample(1.6, 1.61),
         HeartbeatSample(1.75, 1.76), HeartbeatSample(1.9, 1.91)),
        ObservationWindow(0, 2), interval=.15, minimum=5,
    )
    assert evidence.answered == 4
    assert evidence.required == 5


@pytest.mark.platforms("linux")
def test_legacy_timed_call_uses_the_same_rpc_interval(monkeypatch):
    from tests.e2e.core.chaos import _tui_rpc as rpc

    ticks = iter((10.0, 10.25, 20.0, 20.25))
    monkeypatch.setattr(rpc, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    gateway = object.__new__(rpc.TuiGatewayProcess)
    monkeypatch.setattr(gateway, "send", lambda *_args: 1)
    monkeypatch.setattr(gateway, "wait_response", lambda *_args: {"result": {}})
    assert gateway.call_timing("ping", timeout=3) == (10.0, 10.25)
    assert gateway.timed_call("ping", timeout=3) == .25
