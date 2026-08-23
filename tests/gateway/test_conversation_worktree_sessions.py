"""Gateway conversation-worktree ownership boundaries.

These tests exercise the SessionStore boundary rather than a mock-only wrapper:
the observable contract is the persisted session entry's effective cwd and the
manager's durable root identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.conversation_worktree import ConversationWorktreeBinding, ConversationWorktreeError
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource, SessionStore, build_session_context
from gateway.slash_commands import GatewaySlashCommandsMixin


@dataclass
class _Manager:
    root: Path
    fail_new: bool = False

    def __post_init__(self) -> None:
        self.bound_roots: list[str] = []
        self.resolved_roots: list[str] = []

    def bind_new_root_session(
        self, root_session_id: str, *, conversation_kind: str
    ) -> ConversationWorktreeBinding:
        assert conversation_kind == "interactive"
        if self.fail_new:
            raise ConversationWorktreeError("bootstrap did not complete", phase="bootstrap")
        path = self.root / root_session_id
        path.mkdir(parents=True, exist_ok=True)
        self.bound_roots.append(root_session_id)
        return ConversationWorktreeBinding(
            root_session_id=root_session_id,
            path=path,
            branch=f"hermes/session/{root_session_id}",
            base_commit="a" * 40,
            repo_common_dir=self.root,
        )

    def resolve_existing_session(self, root_session_id: str):
        self.resolved_roots.append(root_session_id)
        path = self.root / root_session_id
        if not path.is_dir():
            return None
        return ConversationWorktreeBinding(
            root_session_id=root_session_id,
            path=path,
            branch=f"hermes/session/{root_session_id}",
            base_commit="a" * 40,
            repo_common_dir=self.root,
        )


@pytest.fixture()
def manager(tmp_path) -> _Manager:
    return _Manager(tmp_path / "worktrees")


@pytest.fixture()
def store(tmp_path, manager) -> SessionStore:
    result = SessionStore(
        sessions_dir=tmp_path / "sessions",
        config=GatewayConfig(),
        conversation_worktree_manager_factory=lambda _db: manager,
    )
    # The test exercises the JSON routing mirror. A production SessionDB is
    # neither required for this boundary nor appropriate in a unit test.
    result._db = None
    return result


@pytest.fixture(params=[Platform.DISCORD, Platform("photon")])
def source(request) -> SessionSource:
    return SessionSource(
        platform=request.param,
        chat_id="chat-1",
        chat_type="dm",
        user_id="operator-1",
    )


def test_first_interactive_gateway_session_gets_certified_worktree(store, manager, source):
    """Removing the pre-publish bind must leave this entry without its cwd."""
    entry = store.get_or_create_session(source)

    assert entry.cwd == str(manager.root / entry.session_id)
    assert entry.conversation_worktree["root_session_id"] == entry.session_id
    assert manager.bound_roots == [entry.session_id]


def test_agent_context_uses_certified_session_cwd(store, manager, source):
    """Dropping the task-local cwd bind must route tools back to process cwd."""
    from agent.runtime_cwd import resolve_agent_cwd
    from gateway.run import GatewayRunner

    entry = store.get_or_create_session(source)
    context = build_session_context(source, store.config, entry)
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}

    tokens = runner._set_session_env(context)
    try:
        assert resolve_agent_cwd() == Path(entry.cwd)
    finally:
        runner._clear_session_env(tokens)


def test_ordinary_gateway_reconnect_reuses_durable_worktree(store, manager, source):
    """Replacing existing-entry resolution with a new bind must fail this."""
    first = store.get_or_create_session(source)
    second = store.get_or_create_session(source)

    assert second is first
    assert second.cwd == first.cwd
    assert manager.bound_roots == [first.session_id]
    assert manager.resolved_roots == [first.session_id]


def test_new_binds_distinct_root_before_replacing_old_session(store, manager, source):
    """Moving binding after the routing replacement must fail this contract."""
    first = store.get_or_create_session(source)
    second = store.reset_session(first.session_key)

    assert second is not None
    assert second.session_id != first.session_id
    assert second.cwd != first.cwd
    assert manager.bound_roots == [first.session_id, second.session_id]


def test_failed_new_preserves_old_gateway_boundary(store, manager, source):
    """Publishing reset state before binding would destroy the assertions below."""
    first = store.get_or_create_session(source)
    manager.fail_new = True

    with pytest.raises(ConversationWorktreeError, match="bootstrap did not complete"):
        store.reset_session(first.session_key)

    assert store.lookup_by_session_key(first.session_key) is first
    assert first.cwd == str(manager.root / first.session_id)
    assert manager.bound_roots == [first.session_id]


def test_task_gateway_source_never_allocates_conversation_worktree(store, manager, source):
    """Dropping the explicit task classification must make this allocate a root."""
    entry = store.get_or_create_session(source, conversation_kind="task")

    assert entry.cwd is None
    assert entry.conversation_worktree == {}
    assert manager.bound_roots == []


@pytest.mark.asyncio
async def test_new_bind_failure_preserves_cached_agent_and_generation(tmp_path):
    """Moving cache/generation cleanup before reset preparation must fail this."""
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="chat-1",
        chat_type="dm",
        user_id="operator-1",
    )
    session_key = "agent:main:discord:dm:chat-1"
    old_entry = SimpleNamespace(session_id="old-root")
    cached_agent = object()

    class _FailingAsyncStore:
        async def reset_session(self, key, **_kwargs):
            assert key == session_key
            raise ConversationWorktreeError("bootstrap did not complete", phase="bootstrap")

    class _Runner(GatewaySlashCommandsMixin):
        def __init__(self):
            self.session_store = SimpleNamespace(_entries={session_key: old_entry})
            self._async_session_store = _FailingAsyncStore()
            self._agent_cache = {session_key: cached_agent}
            self.invalidations: list[tuple[str, str]] = []

        @property
        def async_session_store(self):
            return self._async_session_store

        def _session_key_for_source(self, _source):
            return session_key

        def _invalidate_session_run_generation(self, key, *, reason):
            self.invalidations.append((key, reason))

        def _release_running_agent_state(self, _key):
            raise AssertionError("old running state was released before new root was ready")

    runner = _Runner()
    event = MessageEvent(text="/new", source=source, timestamp=datetime.now())

    reply = await runner._handle_reset_command(event)

    assert "conversation worktree setup failed" in str(reply)
    assert runner.session_store._entries[session_key] is old_entry
    assert runner._agent_cache[session_key] is cached_agent
    assert runner.invalidations == []
