"""A retained native resume cannot publish a workspace quarantined before its lease."""

import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from gateway.session_authority import SessionAuthority
from gateway.session_controls import AuthorityConnection
from hermes_state_runtime import begin_runtime_epoch
from tests.agent.test_conversation_worktree_cleanup import prepared_binding


@pytest.mark.asyncio
async def test_retained_resume_rejects_cleanup_winning_before_lease(
    prepared_binding,  # health: allow F811 -- pytest injects the imported actual-Git fixture by parameter name
    monkeypatch,
):
    manager, db, source, _remote, binding, _sibling = prepared_binding
    home = db.db_path.parent
    monkeypatch.setattr(Path, "home", lambda: home / "user-home")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("hermes_cli.banner.prefetch_update_check", lambda: None)
    from tui_gateway import server

    home.joinpath("config.yaml").write_text(
        "conversation_worktree:\n  enabled: true\n"
        f"  source_worktree: {source}\n  worktree_root: {manager._policy.worktree_root}\n"
    )
    db.create_session("root", source="desktop", cwd=str(binding.path))
    db.append_message("root", "user", "Retained fixture history")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(
        server, "_conversation_worktree_manager", lambda **_kwargs: (manager, db, False)
    )
    monkeypatch.setattr(
        server, "_lazy_resume_info", lambda cwd, **_kwargs: {"cwd": cwd, "lazy": True}
    )
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_maybe_schedule_auto_continue", lambda *_args: None)
    monkeypatch.setattr(server, "_child_run_active", lambda *_args: False)
    monkeypatch.setattr(server, "_emit", lambda *_args: None)
    validated, continue_resume = threading.Event(), threading.Event()
    original_acquire = server._acquire_conversation_root_lease

    def delayed_acquire(binding, *, surface):
        validated.set()
        assert continue_resume.wait(10)
        return original_acquire(binding, surface=surface)

    monkeypatch.setattr(server, "_acquire_conversation_root_lease", delayed_acquire)
    epoch = begin_runtime_epoch(db, instance_id="fixture-owner")
    authority = SessionAuthority(
        SimpleNamespace(_draining=False),
        profile_id=str(home),
        instance_id="fixture-owner",
        db=db,
        epoch=epoch,
    )
    connection = AuthorityConnection(
        authority,
        object(),
        {
            "user_id": "owner",
            "provider": "local",
            "profile_id": str(home),
            "instance_id": "fixture-owner",
            "native_bootstrap": True,
        },
        operator=True,
    )
    assert not server._sessions
    resume = asyncio.create_task(
        asyncio.to_thread(
            server._methods["session.resume"],
            "resume",
            {
                "session_id": "root",
                "source": "desktop",
                "lazy": True,
            },
        )
    )
    try:
        assert await asyncio.to_thread(validated.wait, 10)
        assert not authority.sessions
        removed = await connection.dispatch(
            {
                "id": "cleanup",
                "method": "session.worktree_cleanup",
                "params": {"session_id": "root", "action": "quarantine"},
            }
        )
        assert removed["result"]["outcome"] == "retained", removed
        assert removed["result"]["removed"] is False
        assert Path(removed["result"]["retained_path"]).is_dir()
        assert not binding.path.exists()
        continue_resume.set()
        reply = await asyncio.wait_for(resume, 10)

        assert "error" in reply, "resume must not publish a quarantined workspace"
        assert not server._sessions
        assert db.get_conversation_worktree("root").state == "retained"
        assert db.get_messages("root")[0]["content"] == "Retained fixture history"
    finally:
        continue_resume.set()
        await asyncio.gather(resume, return_exceptions=True)
        for record in list(server._sessions.values()):
            if (lease := record.pop("conversation_root_lease", None)) is not None:
                lease.release()
        server._sessions.clear()
        await connection.close()
