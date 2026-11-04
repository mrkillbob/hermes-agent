"""Evidence from serialized heartbeat RPCs inside a fixed observation window."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class HeartbeatSample:
    started: float
    finished: float | None


@dataclass(frozen=True)
class ObservationWindow:
    started: float
    finished: float


@dataclass(frozen=True)
class SamplingEvidence:
    duration: float
    answered: int
    required: int


def sampling_evidence(
    samples: tuple[HeartbeatSample, ...], window: ObservationWindow, *,
    interval: float, minimum: int,
) -> SamplingEvidence:
    """Count replies before cutoff, allowing serialized calls to consume cadence slots."""
    if interval <= 0 or minimum < 1 or window.finished < window.started:
        raise ValueError("invalid heartbeat observation window")
    prior_end = -math.inf
    occupied_overrun = 0.0
    answered = 0
    for sample in samples:
        if sample.started < prior_end or (sample.finished is not None and sample.finished < sample.started):
            raise ValueError("heartbeat samples must be serial and ordered")
        prior_end = sample.finished if sample.finished is not None else math.inf
        end = sample.finished if sample.finished is not None else window.finished
        occupied_overrun += max(
            0.0, min(end, window.finished) - max(sample.started + interval, window.started)
        )
        answered += int(
            sample.started >= window.started
            and sample.finished is not None
            and sample.finished <= window.finished
        )
    duration = window.finished - window.started
    opportunities = math.floor(max(0.0, duration - occupied_overrun) / interval)
    return SamplingEvidence(duration, answered, min(minimum, max(1, opportunities)))


def heartbeat_statistics(
    *, scenario: str, samples: tuple[HeartbeatSample, ...], window: ObservationWindow,
    latencies: tuple[float, ...], failures: tuple[str, ...],
    interval: float, minimum: int, maximum_latency: float,
) -> dict[str, float | int]:
    """Validate responsiveness and the sample opportunities inside the closed window."""
    assert not failures, f"{scenario}: heartbeat RPC failed during the turn: {failures}"
    maximum = round(max(latencies, default=0.0), 3)
    assert maximum < maximum_latency, f"{scenario}: dispatcher blocked {maximum}s during the wedged turn"
    evidence = sampling_evidence(samples, window, interval=interval, minimum=minimum)
    assert evidence.answered >= evidence.required or evidence.duration < 2.0, (
        f"{scenario}: only {evidence.answered}/{evidence.required} heartbeats answered "
        f"inside a {evidence.duration:.1f}s observation window"
    )
    return {
        "heartbeat_max_s": maximum, "heartbeats": len(latencies),
        "heartbeat_window_s": round(evidence.duration, 3),
        "heartbeats_during_window": evidence.answered,
        "heartbeats_required": evidence.required,
    }
