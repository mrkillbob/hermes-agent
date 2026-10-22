"""Native operator cleanup uses the canonical owner and real Git safety gates."""

from types import SimpleNamespace
from dataclasses import replace
import asyncio
from pathlib import Path
import sys
import threading

import pytest

from gateway.session_authority import LiveSession, SessionAuthority
from gateway.session_controls import AuthorityConnection
from hermes_state_runtime import (
    RuntimeStoreError,
    admit_session_input,
    begin_runtime_epoch,
    claim_session_input,
    recover_session_inputs,
)
from agent.conversation_worktree import (
    ConversationWorktreeError,
    ConversationWorktreeManager,
)
from hermes_state import SessionDB
from gateway.session_authorities import SessionAuthorities
from tests.agent.test_conversation_worktree_cleanup import prepared_binding, git


@pytest.fixture
def owner(
    prepared_binding,  # health: allow F811 -- pytest injects the imported prepared_binding fixture by parameter name
    monkeypatch,
):
    manager, db, source, _remote, binding, _sibling = prepared_binding
    home = db.db_path.parent
    monkeypatch.setattr(Path, "home", lambda: home / "user-home")
    monkeypatch.setenv("HERMES_HOME", str(home))
    home.joinpath("config.yaml").write_text(
        "conversation_worktree:\n  enabled: true\n"
        f"  source_worktree: {source}\n  worktree_root: {manager._policy.worktree_root}\n"
    )
    db.create_session(binding.root_session_id, source="test")
    epoch = begin_runtime_epoch(db, instance_id="current")
    authority = SessionAuthority(
        SimpleNamespace(_draining=False),
        profile_id=str(home),
        instance_id="current",
        db=db,
        epoch=epoch,
    )
    identity = {
        "user_id": "owner",
        "provider": "local",
        "profile_id": str(home),
        "instance_id": "current",
        "native_bootstrap": True,
    }
    return authority, identity, binding


async def call(connection, sid="root", **params):
    return await connection.dispatch(
        {
            "id": 1,
            "method": "session.worktree_cleanup",
            "params": {"session_id": sid, **params},
        }
    )


@pytest.mark.asyncio
async def test_canonical_operator_inspects_and_removes_without_adoption(
    owner, monkeypatch
):
    authority, identity, binding = owner

    async def forbidden(*args):
        pytest.fail("cleanup must not restore/adopt historical execution")

    monkeypatch.setattr(
        "gateway.session_local_adoption.adopt_before_restore", forbidden
    )
    connection = AuthorityConnection(authority, object(), identity, operator=True)
    before = authority.db.get_session("root")
    try:
        inspected = await call(connection)
        assert inspected["result"]["allowed"] is True, inspected
        assert binding.path.is_dir()
        removed = await call(connection, action="remove")
        assert removed["result"]["removed"] is True, removed
        assert removed["result"]["state"] == "removed"
        assert authority.db.get_session("root") == before
        assert not authority.sessions
        assert (
            git(binding.path.parent.parent / "stable", "rev-parse", binding.branch)
            == binding.base_commit
        )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_cleanup_intent_refuses_new_admission_before_git_mutation(
    owner, monkeypatch
):
    authority, identity, binding = owner
    entered, release = threading.Event(), threading.Event()
    original = ConversationWorktreeManager.remove_after_explicit_request

    def held(manager, *args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return original(manager, *args, **kwargs)

    monkeypatch.setattr(
        ConversationWorktreeManager, "remove_after_explicit_request", held
    )
    connection = AuthorityConnection(authority, object(), identity, operator=True)
    request = asyncio.create_task(call(connection, action="remove"))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        with pytest.raises(RuntimeStoreError, match="worktree_cleanup_pending"):
            await asyncio.to_thread(
                admit_session_input,
                authority.db,
                epoch=authority.epoch,
                principal_id="owner",
                session_id="root",
                request_id="late",
                payload={"text": "new intent must remain unadmitted"},
            )
        assert binding.path.is_dir()
        release.set()
        response = await asyncio.wait_for(request, timeout=10)
        assert response["result"]["removed"] is True
    finally:
        release.set()
        await connection.close()


@pytest.mark.asyncio
async def test_failed_cleanup_preserves_history_and_removed_identity(
    owner,
    prepared_binding,  # health: allow F811 -- pytest injects the imported prepared_binding fixture by parameter name
):
    authority, identity, _binding = owner
    manager, db, source, _remote, _ready, _sibling = prepared_binding
    failed = ConversationWorktreeManager(
        replace(
            manager._policy,
            bootstrap=True,
            bootstrap_command=(sys.executable, "-c", "raise SystemExit(17)"),
        ),
        db,
    )
    db.create_session("failed", source="test")
    db.append_message("failed", "user", "retained history")
    with pytest.raises(ConversationWorktreeError, match="status 17"):
        failed.bind_new_root_session("failed", conversation_kind="interactive")
    record = db.get_conversation_worktree("failed")
    history = db.get_messages("failed")
    connection = AuthorityConnection(authority, object(), identity, operator=True)
    try:
        response = await call(connection, "failed", action="remove")
        assert response["result"]["removed"] is True, response
        assert db.get_messages("failed") == history
        assert db.get_session("failed") is not None
        removed = db.get_conversation_worktree("failed")
        assert removed.state == "removed"
        assert removed.worktree_path == record.worktree_path
        assert git(source, "rev-parse", record.branch) == record.base_commit
        again = await call(connection, "failed", action="remove")
        assert again["result"]["removed"] is False
        assert again["result"]["reasons"] == ["mismatched identity"]
    finally:
        await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "obligation",
    [
        "mounted",
        "logical_mounted",
        "queued",
        "unknown",
        "logical_queued",
        "logical_unknown",
    ],
)
async def test_cleanup_keeps_live_and_uncertain_canonical_work(owner, obligation):
    authority, identity, binding = owner
    db = authority.db
    sid = "root"
    if obligation.startswith("logical_"):
        sid = "logical"
        db.create_session(sid, source="test")
        db.create_session(
            "physical-child", source="test", parent_session_id="root", chat_id=sid
        )
    if obligation in {"mounted", "logical_mounted"}:
        authority.sessions[sid] = LiveSession(None, "route")
    else:
        admit_session_input(
            db,
            epoch=authority.epoch,
            principal_id="owner",
            session_id=sid,
            request_id="held",
            payload={"text": "do not execute"},
        )
        if obligation.endswith("unknown"):
            claim_session_input(db, epoch=authority.epoch, session_id=sid)
            authority.epoch = begin_runtime_epoch(db, instance_id="next")
            recover_session_inputs(db, epoch=authority.epoch)
    connection = AuthorityConnection(authority, object(), identity, operator=True)
    try:
        response = await call(connection, action="remove")
        assert response["result"]["removed"] is False, response
        assert "active" in response["result"]["reasons"]
        assert binding.path.is_dir()
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_cleanup_cannot_use_another_served_profiles_binding(
    owner,
    prepared_binding,  # health: allow F811 -- pytest injects the imported prepared_binding fixture by parameter name
    tmp_path,
):
    authority, identity, binding = owner
    manager, _db, _source, _remote, _ready, _sibling = prepared_binding
    foreign_home = tmp_path / "foreign-profile"
    foreign_home.mkdir()
    with SessionDB(foreign_home / "state.db") as foreign_db:
        foreign_db.create_session("foreign-only", source="test")
        other = ConversationWorktreeManager(
            replace(manager._policy, worktree_root=foreign_home / "worktrees"),
            foreign_db,
        )
        foreign_binding = other.bind_new_root_session(
            "foreign-only", conversation_kind="interactive"
        )
        foreign_authority = SessionAuthority(
            authority.runner,
            profile_id=str(foreign_home),
            instance_id="current",
            db=foreign_db,
            epoch=begin_runtime_epoch(foreign_db, instance_id="current"),
        )
        registry = SessionAuthorities(authority.profile_id, multiplexed=True)
        registry.add(authority.profile_id, authority)
        registry.add(foreign_home, foreign_authority, name="foreign-profile")
        authority.runner.session_authorities = registry
        connection = AuthorityConnection(authority, object(), identity, operator=True)
        try:
            response = await call(connection, "foreign-only", action="remove")
            assert response["error"]["data"]["reason"] == "not_found", response
            response = await call(
                connection, "foreign-only", profile="foreign-profile", action="remove"
            )
            assert response["error"]["data"]["reason"] == "profile_mismatch", response
            assert foreign_db.get_conversation_worktree("foreign-only").state == "ready"
            assert foreign_binding.path.is_dir() and binding.path.is_dir()
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_transport_cancellation_does_not_abandon_cleanup_worker(
    owner, monkeypatch
):
    authority, identity, binding = owner
    entered, release = threading.Event(), threading.Event()
    original = ConversationWorktreeManager.remove_after_explicit_request

    def held(manager, *args, **kwargs):
        entered.set()
        assert release.wait(timeout=10), "test failed to release cleanup"
        return original(manager, *args, **kwargs)

    monkeypatch.setattr(
        ConversationWorktreeManager, "remove_after_explicit_request", held
    )
    connection = AuthorityConnection(authority, object(), identity, operator=True)
    request = asyncio.create_task(call(connection, action="remove"))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        tasks = tuple(authority._mutation_tasks)
        assert len(tasks) == 1 and not tasks[0].done()
        release.set()
        result = await asyncio.wait_for(asyncio.shield(tasks[0]), timeout=10)
        assert result["removed"] is True
        assert not binding.path.exists()
        assert authority.db.get_conversation_worktree("root").state == "removed"
    finally:
        release.set()
        await connection.close()


@pytest.mark.asyncio
async def test_unknown_native_removal_outcome_is_not_reported_or_replayed_as_success(
    owner, monkeypatch
):
    authority, identity, binding = owner

    def unavailable(*args):
        raise OSError("synthetic ledger write failure")

    monkeypatch.setattr(authority.db, "mark_conversation_worktree_removed", unavailable)
    connection = AuthorityConnection(authority, object(), identity, operator=True)
    try:
        response = await call(connection, action="remove")
        assert response["result"]["removed"] is False, response
        assert response["result"]["reasons"] == ["unknown"]
        assert not binding.path.exists()
        assert authority.db.get_conversation_worktree("root").state == "ready"
        again = await call(connection, action="remove")
        assert again["error"]["data"]["reason"] == "worktree_cleanup_pending"
        with pytest.raises(RuntimeStoreError, match="worktree_cleanup_pending"):
            admit_session_input(
                authority.db,
                epoch=authority.epoch,
                principal_id="owner",
                session_id="root",
                request_id="after-unknown",
                payload={"text": "must stay unadmitted"},
            )
    finally:
        await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,operator,reason",
    [
        ({}, False, "permission_denied"),
        ({"native_bootstrap": False}, True, "permission_denied"),
        ({"instance_id": "stale"}, True, "permission_denied"),
        ({"profile_id": "foreign"}, True, "profile_mismatch"),
    ],
)
async def test_cleanup_refuses_unverified_owner_before_adoption(
    owner, monkeypatch, change, operator, reason
):
    authority, identity, binding = owner

    async def forbidden(*args):
        pytest.fail("refused caller must not adopt a historical session")

    monkeypatch.setattr(
        "gateway.session_local_adoption.adopt_before_restore", forbidden
    )
    connection = AuthorityConnection(
        authority, object(), {**identity, **change}, operator=operator
    )
    try:
        response = await call(connection, action="remove")
        assert response["error"]["data"]["reason"] == reason, response
        assert binding.path.is_dir()
        assert authority.db.get_conversation_worktree("root").state == "ready"
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_inspection_reports_unresolved_intent_instead_of_safe_removal(owner):
    from hermes_state_worktree_cleanup import begin_cleanup

    authority, identity, binding = owner
    begin_cleanup(authority.db, epoch=authority.epoch, root="root")
    connection = AuthorityConnection(authority, object(), identity, operator=True)
    try:
        response = await call(connection)
        assert response["result"]["allowed"] is False
        assert "unknown" in response["result"]["reasons"]
        assert response["result"]["failure_phase"] == "cleanup"
        assert binding.path.is_dir()
    finally:
        await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('profile', [None, 7, {}, False])
async def test_cleanup_profile_selector_matches_declared_nullable_contract(owner, profile):
    authority, identity, binding = owner
    connection = AuthorityConnection(authority, object(), identity, operator=True)
    try:
        response = await call(connection, profile=profile)
        if profile is None:
            assert response['result']['allowed'] is True, response
        else:
            assert response['error']['data']['reason'] == 'invalid_params', response
        assert binding.path.is_dir()
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_cleanup_refuses_foreign_profile_and_unknown_params(owner):
    authority, identity, binding = owner
    connection = AuthorityConnection(authority, object(), identity, operator=True)
    try:
        response = await call(connection, profile="foreign", action="remove")
        assert response["error"]["data"]["reason"] == "profile_mismatch"
        response = await call(connection, action="remove", force=True)
        assert response["error"]["data"]["reason"] == "invalid_params"
        response = await call(connection, action=[])
        assert response["error"]["data"]["reason"] == "invalid_params"
        assert binding.path.is_dir()
    finally:
        await connection.close()
