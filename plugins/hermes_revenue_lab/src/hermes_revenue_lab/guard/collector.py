"""Bounded host observations for the canonical Revenue Lab guard."""

from __future__ import annotations

import http.client
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from hermes_revenue_lab.models.benchmark_guard import GuardSnapshot, collect_guard_snapshot

from .policy import RevenueSnapshot


@dataclass(frozen=True)
class LunaHealthObservation:
    status: Literal["healthy", "unhealthy", "unavailable"]
    latency_ms: float | None


class _HealthResponse(Protocol):
    status: int

    def read(self, amount: int) -> bytes: ...


class _HealthConnection(Protocol):
    def request(self, method: str, path: str) -> None: ...

    def getresponse(self) -> _HealthResponse: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str, int, float], _HealthConnection]


def _connection(host: str, port: int, timeout: float) -> _HealthConnection:
    return http.client.HTTPConnection(host, port, timeout=timeout)


def probe_luna_health(
    *,
    connection_factory: ConnectionFactory = _connection,
    clock: Callable[[], float] = time.monotonic,
) -> LunaHealthObservation:
    """Probe only Luna's loopback health endpoint; never mutate or control it."""

    started = clock()
    connection: _HealthConnection | None = None
    try:
        connection = connection_factory("127.0.0.1", 8787, 0.75)
        connection.request("GET", "/health")
        response = connection.getresponse()
        response.read(4_096)
        latency_ms = max(0.0, (clock() - started) * 1_000.0)
        status = "healthy" if 200 <= response.status < 300 else "unhealthy"
        return LunaHealthObservation(status, latency_ms)
    except (OSError, TimeoutError, http.client.HTTPException):
        return LunaHealthObservation("unavailable", None)
    finally:
        if connection is not None:
            connection.close()


BaseCollector = Callable[..., GuardSnapshot]


def collect_revenue_snapshot(
    *,
    previous_swap_used_bytes: int | None = None,
    allowed_model: str | None = None,
    base_collector: BaseCollector = collect_guard_snapshot,
    health_probe: Callable[[], LunaHealthObservation] = probe_luna_health,
) -> RevenueSnapshot:
    """Adapt bounded HRL-1 evidence into the canonical HRL-4 snapshot."""

    base = base_collector(allowed_model=allowed_model)
    health = health_probe()
    swap_delta: int | None
    if base.swap_used_bytes is None:
        swap_delta = None
    elif previous_swap_used_bytes is None:
        swap_delta = 0
    else:
        swap_delta = max(0, base.swap_used_bytes - previous_swap_used_bytes)
    return RevenueSnapshot(
        luna_process_count=base.luna_process_count,
        revenue_worker_count=base.revenue_model_worker_count,
        load_1m=base.load_1m,
        cpu_count=base.cpu_count,
        memory_free_percent=base.memory_free_percent,
        swap_used_bytes=base.swap_used_bytes,
        swap_total_bytes=base.swap_total_bytes,
        swap_delta_bytes=swap_delta,
        memory_pressure_available=base.memory_free_percent is not None,
        foreign_ollama_model_count=base.foreign_ollama_model_count,
        luna_health_status=health.status,
        luna_health_latency_ms=health.latency_ms,
    )
