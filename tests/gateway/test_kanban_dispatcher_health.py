"""Health classification for the embedded Kanban dispatcher."""

from __future__ import annotations

from gateway.kanban_watchers import _dispatcher_tick_is_unhealthy


def test_full_worker_capacity_is_deferred_not_stuck() -> None:
    """A ready queue behind a full host cap must not count as a bad tick."""
    assert not _dispatcher_tick_is_unhealthy(
        ready_pending=True,
        any_spawned=False,
        capacity_saturated=True,
    )


def test_spawnable_ready_work_without_progress_is_unhealthy() -> None:
    assert _dispatcher_tick_is_unhealthy(
        ready_pending=True,
        any_spawned=False,
        capacity_saturated=False,
    )
