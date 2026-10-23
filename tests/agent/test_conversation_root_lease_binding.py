"""Every native lease consumer refuses a binding retired after ready lookup."""

from dataclasses import replace
import json
from pathlib import Path
import threading

import pytest

from agent.conversation_worktree import (
    ConversationWorktreeError,
    _common_owner_claim_path,
    _root_lease_paths,
    acquire_conversation_root_lease,
)
from gateway.session import SessionStore
from hermes_cli.cli_conversation_worktree_mixin import CLIConversationWorktreeMixin
from tests.agent.test_conversation_worktree_cleanup import prepared_binding


def _acquire(consumer, binding):
    if consumer == "cli":
        return CLIConversationWorktreeMixin._acquire_conversation_root_lease(
            binding, surface="cli"
        )
    if consumer == "store":
        store = object.__new__(SessionStore)
        store._conversation_root_leases_lock = threading.Lock()
        store._conversation_root_leases = {}
        store._ensure_conversation_root_lease(binding)
        return store._conversation_root_leases[binding.root_session_id]
    return acquire_conversation_root_lease(
        root_session_id=binding.root_session_id,
        worktree_path=binding.path,
        repo_common_dir=binding.repo_common_dir,
        surface="test",
    )


@pytest.mark.parametrize("consumer", ["native", "cli", "store"])
@pytest.mark.parametrize(
    "change", ["removed", "replacement", "wrong_root", "wrong_common", "unknown"]
)
def test_retired_or_uncertain_binding_never_publishes_lease(
    prepared_binding,  # health: allow F811 -- pytest injects the imported actual-Git fixture by parameter name
    monkeypatch,
    consumer,
    change,
):
    manager, db, _source, _remote, original, _sibling = prepared_binding
    monkeypatch.setattr(Path, "home", lambda: db.db_path.parent / "user-home")
    monkeypatch.setenv("HERMES_HOME", str(db.db_path.parent))
    binding = manager.resolve_existing_session(original.root_session_id)
    assert binding is not None
    claim_path = _common_owner_claim_path(binding.repo_common_dir, binding.path)
    if change in {"removed", "replacement"}:
        assert manager.quarantine_after_explicit_request(binding.root_session_id).outcome == 'retained'
        if change == "replacement":
            binding.path.mkdir()
            binding.path.joinpath("unique.txt").write_text("Preserve this replacement")
    elif change == "wrong_root":
        claim = json.loads(claim_path.read_text())
        claim["root_session_id"] = "another-root"
        claim_path.write_text(json.dumps(claim))
    elif change == "wrong_common":
        binding = replace(
            binding, repo_common_dir=binding.repo_common_dir / "another-common"
        )
    else:
        claim_path.write_text("{unreadable identity")

    with pytest.raises(ConversationWorktreeError, match="ownership is unavailable|retention"):
        _acquire(consumer, binding)

    state, _lock = _root_lease_paths(binding.repo_common_dir, binding.root_session_id)
    assert not state.exists(), "a refused binding must never publish liveness"
    if change == "replacement":
        assert (
            binding.path.joinpath("unique.txt").read_text()
            == "Preserve this replacement"
        )


@pytest.mark.parametrize("consumer", ["native", "cli", "store"])
def test_acquired_lease_prevents_cleanup_until_release(
    prepared_binding,  # health: allow F811 -- pytest injects the imported actual-Git fixture by parameter name
    monkeypatch,
    consumer,
):
    manager, db, _source, _remote, binding, _sibling = prepared_binding
    monkeypatch.setattr(Path, "home", lambda: db.db_path.parent / "user-home")
    monkeypatch.setenv("HERMES_HOME", str(db.db_path.parent))
    lease = _acquire(consumer, binding)
    try:
        result = manager.quarantine_after_explicit_request(binding.root_session_id)
        assert not result.removed
        assert "active" in result.verdict.reasons
        assert binding.path.is_dir()
        assert db.get_conversation_worktree(binding.root_session_id).state == "ready"
    finally:
        lease.release()
    assert manager.quarantine_after_explicit_request(binding.root_session_id).outcome == 'retained'
