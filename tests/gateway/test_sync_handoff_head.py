"""The upstream handoff head and fork atomic verified route compose."""

from types import SimpleNamespace

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner
from gateway.session import AsyncSessionStore, SessionSource, SessionStore


@pytest.mark.asyncio
async def test_handoff_uses_current_head_cwd_in_one_routing_flight(monkeypatch):
    seen = []

    class _DB:
        def get_session(self, session_id):
            assert session_id == "current-head"
            return {"cwd": "/tmp/current-head-workspace"}

    class _Store:
        _db = _DB()

        def resolve_task_owned_workspace(self, session_id, cwd):
            seen.append(("verify", session_id, cwd))
            return cwd, {"root_session_id": session_id}

        def get_or_create_session_and_switch(self, source, session_id, **kwargs):
            seen.append(("atomic", session_id, kwargs))
            return SimpleNamespace(session_id=session_id)

        def get_or_create_session(self, *args, **kwargs):
            raise AssertionError("handoff must not split its routing flight")

        def switch_session(self, *args, **kwargs):
            raise AssertionError("handoff must not split its routing flight")

    async def current_head(_runner, session_id):
        assert session_id == "earlier-segment"
        return None, "local-owner", "current-head"

    monkeypatch.setattr("gateway.session_route_recovery.conversation_head", current_head)
    runner = object.__new__(GatewayRunner)
    runner.session_store = _Store()
    runner._async_session_store = AsyncSessionStore(runner.session_store)
    source = SessionSource(platform=Platform.DISCORD, chat_id="channel", chat_type="dm")
    await runner._handoff_repoint(
        SimpleNamespace(source=source), "route", "earlier-segment", "/tmp/earlier-workspace"
    )
    assert seen == [
        ("verify", "current-head", "/tmp/current-head-workspace"),
        ("atomic", "current-head", {
            "conversation_kind": "task", "preserve_prompt_pin": False,
            "persisted_cwd": "/tmp/current-head-workspace",
        }),
    ]


def test_reset_publishes_pending_row_until_transition_settles(tmp_path, monkeypatch):
    """A racing inbound must not heal a newly published route before its row exists."""
    store = SessionStore(tmp_path / "sessions", GatewayConfig(),
                         conversation_worktree_manager_factory=lambda _db: None)
    store._db = None
    source = SessionSource(platform=Platform.DISCORD, chat_id="channel", chat_type="dm")
    old = store.get_or_create_session(source)

    class _MissingRowDB:
        def get_session(self, _session_id):
            return None

    store._db = _MissingRowDB()
    pending_observations = []

    def finish(_session_key, *, create_kwargs, **_kwargs):
        new_id = create_kwargs["session_id"]
        pending_observations.append((
            new_id in store._rows_pending,
            store._is_session_ended_in_db(new_id, old.session_key),
        ))

    monkeypatch.setattr(store, "_finish_route_transition", finish)
    new = store.reset_session(old.session_key)
    assert pending_observations == [(True, False)]
    assert new.session_id not in store._rows_pending
    assert store._is_session_ended_in_db(new.session_id, old.session_key) is True
