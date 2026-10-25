"""Routed children retain their own cleanup and backend visibility contracts."""
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.tools.test_delegation_profile_route import two_profiles, _set_config

__all__ = ["two_profiles"]


@pytest.mark.parametrize("deferred", [False, True])
def test_profile_child_close_releases_its_scoped_computer_use_backend(two_profiles, monkeypatch, deferred):
    from hermes_constants import get_hermes_home
    from tools.computer_use import tool as cua
    from tools.delegate_tool_child_run import _close_child, _defer_close_after_timeout
    from tools.delegate_tool_routes import delegated_profile_scope

    launch, coder = two_profiles
    backend = SimpleNamespace(stop=Mock())
    with delegated_profile_scope(coder):
        key = cua._scoped_sid("sync-child")
    monkeypatch.setattr(cua, "_backends", {key: backend})

    class Child:
        _delegate_profile_home = coder

        def close(self):
            cua.release_computer_use_session("sync-child")

    if deferred:
        done = Future()
        _defer_close_after_timeout(Child(), done)
        done.set_result(None)
    else:
        _close_child(Child(), "fixture close")
    backend.stop.assert_called_once_with()
    assert key not in cua._backends
    assert get_hermes_home() == launch


def test_lease_and_relay_cleanup_use_child_profile_and_restore_parent(two_profiles, monkeypatch):
    from agent import relay_runtime
    from hermes_constants import get_hermes_home
    from tools.delegate_tool_child_run import _ChildRun

    launch, coder = two_profiles
    seen = []
    child = SimpleNamespace(_delegate_profile_home=coder, session_id="sync-child", close=lambda: None)
    parent = SimpleNamespace()
    run = _ChildRun(child, parent, 0, "fixture", None, None)
    pool = SimpleNamespace(release_lease=lambda _lease: seen.append(("lease", get_hermes_home())))
    runtime = SimpleNamespace(unregister_subagent=lambda _record: seen.append(("relay", get_hermes_home())))
    monkeypatch.setattr(relay_runtime, "get_runtime", lambda **kwargs: runtime)
    monkeypatch.setattr(relay_runtime.SESSION_COORDINATOR, "has_active_turn", lambda **kwargs: False)
    monkeypatch.setattr("tools.code_kernel.shutdown_kernels_for_delegated_child", lambda _sid: None)
    run.cleanup(heartbeat=SimpleNamespace(stop=lambda: None), child_pool=pool,
                leased_cred_id="lease", close_deferred=False)
    assert seen == [("lease", coder), ("relay", coder)]
    assert get_hermes_home() == launch


@pytest.mark.parametrize("backend", ["local", "ssh", "docker"])
def test_host_worktree_is_only_given_to_a_child_that_can_see_it(two_profiles, monkeypatch, backend):
    from hermes_constants import get_hermes_home
    from tools import subagent_worktree, terminal_tool
    from tools.delegate_tool_child_run import _ChildRun
    from tools.delegate_tool_routes import child_profile_scope

    launch, coder = two_profiles
    _set_config(coder, terminal={"backend": backend, "cwd": "/remote/repo"})
    parent = SimpleNamespace(_current_task_id="sync-parent")
    child = SimpleNamespace(_delegate_profile_home=coder)
    monkeypatch.setattr("tools.delegate_tool._get_worktree_isolation", lambda: True)
    monkeypatch.setattr("tools.delegate_tool._resolve_workspace_hint", lambda _parent: "/parent/repo")
    created = []

    def create(_cwd, **kwargs):
        created.append(get_hermes_home())
        return {"path": "/parent/worktree", "branch": "subagent/fixture", "repo_root": "/parent/repo"}

    monkeypatch.setattr(subagent_worktree, "create_subagent_worktree", create)
    terminal_tool.record_session_cwd("sync-parent", "/parent/repo")
    run = _ChildRun(child, parent, 0, "fixture", None, None)
    try:
        run.seed_workspace()
        with child_profile_scope(child):
            cwd = terminal_tool.get_session_cwd(run.child_task_id)
        if backend == "local":
            assert created == [launch]  # creation/provenance remains the parent's
            assert cwd == "/parent/worktree"
            assert run.worktree_info is not None
        else:
            assert created == []
            assert run.worktree_info is None
            assert cwd is None  # use the target backend's configured starting directory
            assert "/parent/worktree" not in run.goal
        assert get_hermes_home() == launch
    finally:
        terminal_tool.clear_session_cwd("sync-parent")
        with child_profile_scope(child):
            terminal_tool.clear_session_cwd(run.child_task_id)


@pytest.mark.parametrize("mode", ["hard", "soft", "soft-fallback"])
def test_parent_lifecycle_closes_children_in_their_own_profile(two_profiles, mode):
    import threading
    from hermes_constants import get_hermes_home
    from run_agent import AIAgent

    launch, coder = two_profiles
    seen = []

    class Child:
        _delegate_profile_home = coder

        def release_clients(self):
            seen.append(("soft", get_hermes_home()))
            if mode == "soft-fallback":
                raise RuntimeError("fixture soft release failed")

        def close(self):
            seen.append(("hard", get_hermes_home()))

    parent = SimpleNamespace(_active_children=[Child()], _active_children_lock=threading.Lock())
    AIAgent._close_active_children(parent, soft=mode != "hard")
    expected = ["hard"] if mode == "hard" else ["soft", "hard"] if mode == "soft-fallback" else ["soft"]
    assert seen == [(event, coder) for event in expected]
    assert parent._active_children == []
    assert get_hermes_home() == launch
