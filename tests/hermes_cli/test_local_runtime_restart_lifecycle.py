"""Watchdog restart lifecycle: a restart that never becomes healthy must not strand its router.

Offline: `_spawn` starts a stand-in process, so process closure is real but no engine or model
is involved.
"""

from __future__ import annotations

import subprocess
import sys

from hermes_cli.local_runtime import supervisor


def _watchdog_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "runtimes_root", lambda: tmp_path)
    sup = supervisor.LlamaServerSupervisor(tmp_path, tmp_path, port=59996)
    crashed = subprocess.Popen([sys.executable, "-c", "pass"])
    crashed.wait(timeout=10)
    sup.proc = crashed
    spawned: list[subprocess.Popen] = []

    def spawn():
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        spawned.append(proc)
        sup.proc = proc

    monkeypatch.setattr(sup, "_spawn", spawn)
    monkeypatch.setattr(sup, "_reap_orphaned_children", lambda: None)
    monkeypatch.setattr(sup._stop_event, "wait", lambda *a: False)
    polls = []
    real_sleep = supervisor.time.sleep

    def bounded_sleep(seconds):
        # The watchdog polls a live router forever; bound it so a regression fails instead of hanging.
        if seconds != 2:  # only the watchdog's own poll; subprocess.wait sleeps through the same module
            return real_sleep(seconds)
        polls.append(seconds)
        if len(polls) > 3:
            sup.stop()

    monkeypatch.setattr(supervisor.time, "sleep", bounded_sleep)
    return sup, spawned


def test_unhealthy_restart_is_closed_and_retried(tmp_path, monkeypatch):
    sup, spawned = _watchdog_fixture(tmp_path, monkeypatch)
    health_calls = []

    def wait_health(*args, **kwargs):
        health_calls.append(len(spawned))
        if len(health_calls) == 1:
            raise TimeoutError("router alive but never healthy")
        sup.stop()  # second restart is healthy; end the watchdog

    monkeypatch.setattr(sup, "_wait_health", wait_health)
    try:
        sup._watch()
        assert len(spawned) == 2, "a live-but-unhealthy restart was never recycled"
        assert spawned[0].poll() is not None, "the unhealthy router kept running unsupervised"
    finally:
        for proc in spawned:
            proc.kill()
            proc.wait(timeout=10)


def test_failed_readiness_proof_keeps_the_healthy_router(tmp_path, monkeypatch):
    sup, spawned = _watchdog_fixture(tmp_path, monkeypatch)
    sup.primary_model = "m"
    monkeypatch.setattr(sup, "_wait_health", lambda *a, **k: None)

    def not_ready(model_id):
        assert spawned[0].poll() is None
        sup.stop()
        return False

    monkeypatch.setattr(sup, "ensure_model_ready", not_ready)
    try:
        sup._watch()
        assert len(spawned) == 1, "a model that failed its touch request restarted a healthy router"
    finally:
        for proc in spawned:
            proc.kill()
            proc.wait(timeout=10)


def test_cleanup_failure_does_not_end_the_watchdog(tmp_path, monkeypatch):
    sup, spawned = _watchdog_fixture(tmp_path, monkeypatch)

    def wait_health(*args, **kwargs):
        raise TimeoutError("router alive but never healthy")

    real_terminate, failures = sup._terminate_tree, []

    def broken_terminate(proc, **kwargs):
        if not failures:  # only the restart cleanup fails; stop() must still close the router
            failures.append(proc.pid)
            raise PermissionError("cannot terminate")
        return real_terminate(proc, **kwargs)

    monkeypatch.setattr(sup, "_wait_health", wait_health)
    monkeypatch.setattr(sup, "_terminate_tree", broken_terminate)
    try:
        sup._watch()  # an escaping cleanup error would raise here and end the watchdog thread
        assert failures == [spawned[0].pid]
    finally:
        for proc in spawned:
            proc.kill()
            proc.wait(timeout=10)
