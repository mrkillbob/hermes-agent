"""RED tests for #120546: background WSL work leaves ownerless PIDs.

A delegate child spawns background processes under its RAW task id
(``sa-...``) while the registry stores the COLLAPSED container key
(``task_id``, e.g. ``"default"`` on local backends). Queries filtered on the
container key only, so the owner saw zero live processes while GPU work ran,
and kills/reaps missed them. Queries must match the ownership chain
(container key OR raw spawning owner). WSL-launched commands must also carry
a durable marker: the recorded host PID is the short-lived ``wsl.exe``
launcher, never the Linux-side workers.
"""

import time
from unittest.mock import patch

from tools.process_registry import ProcessRegistry, ProcessSession


def _owned_session(sid="proc_own1", container="default", owner="sa-1-abc123",
                   command="sleep 30", **kw):
    return ProcessSession(
        id=sid, command=command, task_id=container, owner_task_id=owner,
        started_at=kw.pop("started_at", time.time()), **kw)


def test_list_surfaces_owner_spawned_process_despite_collapsed_container_key():
    reg = ProcessRegistry()
    reg._running["proc_own1"] = _owned_session()
    entries = reg.list_sessions(task_id="sa-1-abc123")
    assert [e["session_id"] for e in entries] == ["proc_own1"]
    assert entries[0]["owner_task_id"] == "sa-1-abc123"


def test_list_still_matches_container_key():
    reg = ProcessRegistry()
    reg._running["proc_c1"] = _owned_session(
        sid="proc_c1", container="default", owner="default")
    assert [e["session_id"] for e in reg.list_sessions(task_id="default")] == ["proc_c1"]


def test_has_active_processes_matches_raw_owner():
    reg = ProcessRegistry()
    reg._running["proc_own1"] = _owned_session()
    assert reg.has_active_processes("sa-1-abc123") is True
    assert reg.has_active_processes("sa-9-unrelated") is False


def test_snapshot_running_ids_matches_raw_owner():
    reg = ProcessRegistry()
    reg._running["proc_own1"] = _owned_session()
    assert reg.snapshot_running_ids("sa-1-abc123") == frozenset({"proc_own1"})
    assert reg.snapshot_running_ids("sa-9-unrelated") == frozenset()


def test_kill_all_targets_owner_spawned_process():
    reg = ProcessRegistry()
    reg._running["proc_own1"] = _owned_session()
    with patch.object(reg, "kill_process",
                      return_value={"status": "killed"}) as kill:
        assert reg.kill_all("sa-1-abc123", source="t") == 1
    kill.assert_called_once_with("proc_own1", source="t", consume_output=False)


def test_wsl_launcher_command_stamped_at_spawn():
    reg = ProcessRegistry()
    wsl = reg._new_session("wsl.exe -e bash -lc 'ninfer-serve --port 8080'",
                           "default", "sa-1-abc123", "", ".")
    plain = reg._new_session("python -m pytest -x", "default", "sa-1-abc123", "", ".")
    assert wsl.wsl_chain is True
    assert plain.wsl_chain is False


def test_wsl_launcher_forms_detected():
    reg = ProcessRegistry()
    for cmd in ("wsl -e bash -c 'x'", "wsl --exec foo", "wsl -d Ubuntu -- bar",
                "C:\\Windows\\System32\\wsl.exe -e foo"):
        assert reg._new_session(cmd, "d", "o", "", ".").wsl_chain is True, cmd
    for cmd in ("python -m wsl_tool", "echo wsl.exe", "mywsl -e x"):
        assert reg._new_session(cmd, "d", "o", "", ".").wsl_chain is False, cmd


def test_list_entry_surfaces_wsl_chain_hint():
    reg = ProcessRegistry()
    reg._running["proc_w"] = _owned_session(
        sid="proc_w", command="wsl.exe -e bash -lc 'ninfer-serve'")
    reg._running["proc_w"].wsl_chain = True
    (entry,) = reg.list_sessions(task_id="sa-1-abc123")
    assert entry["wsl_chain"] is True
    assert "wsl" in entry["wsl_note"].lower()


def test_idle_reaper_keeps_the_environment_of_another_raw_owner(monkeypatch):
    from unittest.mock import Mock
    import tools.terminal_tool as terminal
    import tools.process_registry as registry_module
    from tools.terminal_tool_lifecycle import _cleanup_inactive_envs

    reg = ProcessRegistry()
    reg._running["proc_own1"] = _owned_session(container="shared-env", owner="delegate-owner")
    busy, idle = Mock(), Mock()
    monkeypatch.setattr(registry_module, "process_registry", reg)
    monkeypatch.setattr(terminal, "_active_environments", {"shared-env": busy, "idle-env": idle})
    monkeypatch.setattr(terminal, "_last_activity", {"shared-env": time.time() - 600, "idle-env": time.time() - 600})
    monkeypatch.setattr(terminal, "_creation_locks", {})
    assert not reg.has_active_processes("shared-env")  # termination remains raw-owner scoped
    assert reg.has_active_processes("delegate-owner")
    _cleanup_inactive_envs(lifetime_seconds=300)
    assert terminal._active_environments == {"shared-env": busy}
    busy.cleanup.assert_not_called()
    idle.cleanup.assert_called_once_with()
