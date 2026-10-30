"""Detached app turns keep the certified workspace of the owning profile."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.conversation_worktree import ConversationWorktreeError
from gateway.config import GatewayConfig, Platform
from gateway.session import SessionSource, SessionStore, build_session_context
from gateway.session_detached import detached_entry, detached_key, live_root_held


def _source(profile: str) -> SessionSource:
    return SessionSource(
        platform=Platform.DISCORD, chat_id="chat", chat_type="dm", user_id="user",
        detached_route=f"agent:{profile}:detached:root",
    )


class _DB:
    def __init__(self, cwd: str):
        self.cwd = cwd
        self.reads = []

    def get_session(self, session_id):
        self.reads.append(session_id)
        if session_id == "root":
            return {"started_at": 1, "cwd": self.cwd}
        if session_id == "tip":
            return {"parent_session_id": "root", "cwd": self.cwd}
        return None

    def get_compression_tip(self, session_id):
        return "tip"

    def get_conversation_worktree(self, session_id):
        return None

    def is_explicit_fork_child(self, session_id):
        return False


class _Manager:
    def __init__(self, path: Path, ready=True):
        self.path = path
        self.ready = ready
        self.resolved = []

    def resolve_existing_session(self, session_id):
        self.resolved.append(session_id)
        if session_id != "root" or not self.ready:
            return None
        return SimpleNamespace(
            root_session_id="root", path=self.path, branch="branch",
            base_commit="a" * 40, repo_common_dir=self.path.parent,
        )

    def bind_new_root_session(self, *args, **kwargs):
        pytest.fail("detached turn attempted to create a new workspace root")


def test_live_detached_conversation_holds_lease_until_retirement(tmp_path, monkeypatch):
    from agent.conversation_worktree import ConversationWorktreeBinding

    class NewRootManager:
        def bind_new_root_session(self, root_session_id, *, conversation_kind):
            path = tmp_path / root_session_id
            path.mkdir()
            return ConversationWorktreeBinding(
                root_session_id=root_session_id, path=path, branch="branch",
                base_commit="a" * 40, repo_common_dir=tmp_path,
            )

    class Lease:
        released = False

        def release(self):
            self.released = True

    store = SessionStore(
        tmp_path / "sessions", GatewayConfig(),
        conversation_worktree_manager_factory=lambda db: NewRootManager(),
    )
    store._db = None

    def acquire(binding):
        store._conversation_root_leases.setdefault(binding.root_session_id, Lease())
        return True

    monkeypatch.setattr(store, "_ensure_conversation_root_lease", acquire)
    source = replace(_source("main"), detached_route=None)
    first = store.get_or_create_session(source)
    first_lease = store._conversation_root_leases[first.session_id]
    live = SimpleNamespace(source=source, route=first.session_key)
    authority = SimpleNamespace(
        sessions={first.session_id: live}, logical_owner=lambda session_id: session_id,
    )
    runner = SimpleNamespace(session_authority=authority)
    store._conversation_live_root_check = lambda root: live_root_held(runner, root)

    second = store.reset_session(first.session_key)  # /new replaces route before owner-loop detach
    assert second.session_id != first.session_id
    assert not first_lease.released
    live.route = detached_key(first.session_key, first.session_id)
    live.source = replace(source, detached_route=live.route)
    assert store.release_conversation_root_lease(first.session_id) is False
    assert not first_lease.released

    authority.sessions.pop(first.session_id)  # canonical retirement removed the live mount
    store.release_unreferenced_conversation_root_leases()
    assert first_lease.released
    assert first.session_id not in store._conversation_root_leases


def test_detached_entry_uses_owning_profile_binding_and_lease(tmp_path, monkeypatch):
    path = tmp_path / "certified"
    path.mkdir()
    wrong = _DB(str(tmp_path / "wrong"))
    owner = _DB(str(path))
    manager = _Manager(path)
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    monkeypatch.setattr(store, "_db_for_key", lambda key: owner if ":secondary:" in key else wrong)
    monkeypatch.setattr(store, "_conversation_worktree_manager", lambda key: manager)
    leases = []
    monkeypatch.setattr(store, "_ensure_conversation_root_lease", lambda binding: leases.append(binding.root_session_id) or True)

    source = _source("secondary")
    entry = detached_entry(store, source)
    context = build_session_context(source, GatewayConfig(), entry)
    assert context.cwd == str(path)
    assert context.conversation_worktree["root_session_id"] == "root"
    assert entry.session_id == "tip"
    assert leases == ["root"]
    assert manager.resolved == ["tip", "root"]
    assert wrong.reads == []
    assert store._entries == {}


@pytest.mark.parametrize("ready,recorded", [(False, True), (True, False)])
def test_detached_entry_refuses_missing_or_mismatched_binding(tmp_path, monkeypatch, ready, recorded):
    path = tmp_path / "certified"
    path.mkdir()
    db = _DB(str(path) if recorded else str(tmp_path / "other"))
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    monkeypatch.setattr(store, "_db_for_key", lambda key: db)
    monkeypatch.setattr(store, "_conversation_worktree_manager", lambda key: _Manager(path, ready=ready))
    monkeypatch.setattr(store, "_ensure_conversation_root_lease", lambda binding: pytest.fail("invalid binding leased"))
    with pytest.raises(ConversationWorktreeError):
        detached_entry(store, _source("main"))
    assert store._entries == {}
