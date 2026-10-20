"""Uncontained native tree uncertainty must survive router and owner failure.

All native spawns are tripwires; no model or engine is launched by these tests.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from hermes_cli.local_runtime import bootstrap, recovery, supervisor, tree_custody


@pytest.mark.platforms("posix")
def test_spawn_intent_is_exclusive_and_survives_uncertain_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "runtimes_root", lambda: tmp_path)
    monkeypatch.setattr(supervisor, "_direct_io_args", lambda exe: ())
    sup = supervisor.LlamaServerSupervisor(tmp_path / "engine", tmp_path, port=59996)
    sup._owned_job_closed = True  # settlement of an earlier tree cannot cover this attempt
    seen = []

    def uncertain_spawn(*args, **kwargs):
        state = json.loads(supervisor.state_path().read_text())
        assert state["tree_custody"] == "unverified"
        seen.append(state)
        raise OSError("spawn outcome uncertain")

    monkeypatch.setattr(supervisor, "spawn_server", uncertain_spawn)
    try:
        with pytest.raises(OSError, match="outcome uncertain"):
            sup._spawn()
        assert sup._owned_job_closed is False
        sup.proc = SimpleNamespace(pid=99999999, poll=lambda: 1)
        sup._watch()  # uncertain spawn must not reuse the earlier settlement proof
        assert len(seen) == 1
        assert json.loads(supervisor.state_path().read_text()) == seen[0]
        replacement = supervisor.LlamaServerSupervisor(tmp_path / "engine", tmp_path, port=59995)
        try:
            probes = []
            monkeypatch.setattr(supervisor, "_direct_io_args", lambda exe: probes.append(exe))
            with pytest.raises(RuntimeError, match="replacement refused"):
                replacement._spawn()
            assert len(seen) == 1
            assert probes == [], "quarantined custody still reached the native help probe"
        finally:
            replacement.stop()
        assert json.loads(supervisor.state_path().read_text()) == seen[0]
    finally:
        sup.stop()


@pytest.mark.platforms("posix")
def test_state_reservation_failure_prevents_native_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "runtimes_root", lambda: tmp_path)
    monkeypatch.setattr(supervisor, "_direct_io_args", lambda exe: ())
    sup = supervisor.LlamaServerSupervisor(tmp_path / "engine", tmp_path, port=59996)
    spawned = []
    monkeypatch.setattr(supervisor, "spawn_server", lambda *a, **k: spawned.append(True))
    monkeypatch.setattr(tree_custody.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk failure")))
    try:
        with pytest.raises(OSError, match="disk failure"):
            sup._spawn()
        assert spawned == []
        assert supervisor.state_path().exists()
    finally:
        sup.stop()


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("record", ["{broken", "{}", '{"pid":99999999}',
                                    '{"tree_custody":"unverified","owner_pid":99999999}'])
def test_owner_replacement_refuses_dead_or_uncertain_record(tmp_path, monkeypatch, record):
    monkeypatch.setattr(supervisor, "runtimes_root", lambda: tmp_path)
    supervisor.state_path().write_text(record)
    monkeypatch.setattr(recovery.psutil, "pid_exists", lambda pid: False)
    assert bootstrap._incumbent_allows_boot() is False
    assert supervisor.state_path().read_text() == record


@pytest.mark.platforms("posix")
def test_dead_router_watchdog_never_scans_or_respawns(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "runtimes_root", lambda: tmp_path)
    intent = tree_custody.reserve_uncontained_tree(supervisor.state_path())
    sup = supervisor.LlamaServerSupervisor(tmp_path / "engine", tmp_path, port=59996)
    sup.proc = SimpleNamespace(pid=99999999, poll=lambda: 1)
    reached = []
    monkeypatch.setattr(sup, "_reap_orphaned_children", lambda: reached.append("scan"))
    monkeypatch.setattr(sup, "_spawn", lambda: reached.append("spawn"))
    sup._watch()
    assert reached == []
    assert sup._stop_event.is_set()
    sup.stop()
    assert json.loads(supervisor.state_path().read_text()) == intent


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("replacement", [None, "{broken", '{"spawn_ticket":"other"}'])
def test_router_publication_refuses_lost_or_changed_intent(tmp_path, replacement):
    path = tmp_path / "server.json"
    intent = tree_custody.reserve_uncontained_tree(path)
    if replacement is None:
        path.unlink()
    else:
        path.write_text(replacement)
    with pytest.raises(RuntimeError, match="custody record"):
        tree_custody.verify_spawn_intent(path, intent)


@pytest.mark.platforms("posix")
def test_matching_spawn_intent_accepts_editor_bom(tmp_path):
    path = tmp_path / "server.json"
    intent = tree_custody.reserve_uncontained_tree(path)
    path.write_text(json.dumps(intent), encoding="utf-8-sig")
    tree_custody.verify_spawn_intent(path, intent)


@pytest.mark.parametrize("close_fails", [False, True])
def test_containment_settlement_retains_proof_or_failed_handle(tmp_path, monkeypatch, close_fails):
    """The containment interface is synthetic; actual Job behavior is tested on Windows."""
    monkeypatch.setattr(supervisor, "runtimes_root", lambda: tmp_path)
    sup = supervisor.LlamaServerSupervisor(tmp_path / "engine", tmp_path, port=59996)
    sup.proc = SimpleNamespace(pid=101, poll=lambda: None)
    monkeypatch.setattr(sup, "_terminate_tree", lambda proc: None)

    def close():
        if close_fails:
            raise OSError("close failed")

    owned_handle = SimpleNamespace(close=close)
    sup._job = owned_handle
    sup._close_unhealthy_restart()
    assert sup._owned_job_closed is (not close_fails)
    if close_fails:
        assert sup._job is owned_handle
    else:
        assert sup._job is None
        sup._reap_orphaned_children()  # already-settled containment permits retry
