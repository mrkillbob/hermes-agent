"""Cleanup reserves an idle retained root before Git, without discarding history."""

import pytest

import hermes_state_runtime as rt
from hermes_state_worktree_cleanup import (
    INTENT_PREFIX,
    begin_cleanup,
    release_refused_cleanup,
)
from tests.agent.test_conversation_worktree_cleanup import prepared_binding
from tests.hermes_state.test_runtime_recovery import legacy, row


@pytest.fixture
def store(
    prepared_binding,  # health: allow F811 -- pytest injects the imported prepared_binding fixture by parameter name
):
    _manager, db, _source, _remote, _binding, _sibling = prepared_binding
    db.create_session("root", source="test")
    return db, rt.begin_runtime_epoch(db, instance_id="owner")


def admit(db, epoch, sid="root", request="one"):
    return rt.admit_session_input(
        db,
        epoch=epoch,
        principal_id="owner",
        session_id=sid,
        request_id=request,
        payload={"text": request},
    )


@pytest.mark.parametrize("state", ["queued", "started", "unknown", "worker"])
def test_existing_accepted_work_refuses_intent_atomically(store, state):
    db, epoch = store
    if state == "worker":
        rt.register_worker_execution(
            db,
            epoch=epoch,
            execution_id="worker",
            session_id="root",
            generation=0,
            kind="compute",
            adoption_secret="fixture",
        )
    else:
        admit(db, epoch)
        if state != "queued":
            rt.claim_session_input(db, epoch=epoch, session_id="root")
        if state == "unknown":
            epoch = rt.begin_runtime_epoch(db, instance_id="next")
            rt.recover_session_inputs(db, epoch=epoch)
    with pytest.raises(
        rt.RuntimeStoreError,
        match="unknown_execution" if state == "unknown" else "session_busy",
    ):
        begin_cleanup(db, epoch=epoch, root="root")
    assert db.get_conversation_worktree("root").state == "ready"
    assert (
        db._read_one("SELECT 1 FROM state_meta WHERE key=?", (INTENT_PREFIX + "root",))
        is None
    )


@pytest.mark.parametrize("target", ["root", "child", "logical"])
def test_intent_blocks_root_and_physical_or_logical_continuations(store, target):
    db, epoch = store
    db.create_session("logical", source="test")
    db.create_session(
        "child", source="test", parent_session_id="root", chat_id="logical"
    )
    begin_cleanup(db, epoch=epoch, root="root")
    with pytest.raises(rt.RuntimeStoreError, match="worktree_cleanup_pending"):
        admit(db, epoch, target)
    assert not rt.list_session_admissions(db, session_id=target)
    db.create_session("unrelated", source="test")
    assert admit(db, epoch, "unrelated")["status"] == "queued"


def test_terminal_retry_is_preserved_but_new_work_is_not_admitted(store):
    db, epoch = store
    accepted = admit(db, epoch)
    claim = rt.claim_session_input(db, epoch=epoch, session_id="root")
    rt.settle_session_input(
        db,
        epoch=epoch,
        admission_id=accepted["admission_id"],
        generation=claim["generation"],
        outcome="completed",
    )
    intent = begin_cleanup(db, epoch=epoch, root="root")
    assert admit(db, epoch)["status"] == "terminal"
    with pytest.raises(rt.RuntimeStoreError, match="worktree_cleanup_pending"):
        admit(db, epoch, request="two")
    release_refused_cleanup(db, epoch=epoch, root="root", intent=intent)
    assert admit(db, epoch, request="two")["status"] == "queued"


def test_unsettled_steer_transfer_still_owns_its_workspace(store):
    db, epoch = store
    first = admit(db, epoch)
    claim = rt.claim_session_input(db, epoch=epoch, session_id="root")
    second = admit(db, epoch, request="two")
    rt.steer_session_input(
        db,
        epoch=epoch,
        admission_id=second["admission_id"],
        generation=claim["generation"],
    )
    rt.settle_session_input(
        db,
        epoch=epoch,
        admission_id=first["admission_id"],
        generation=claim["generation"],
        outcome="completed",
    )
    with pytest.raises(rt.RuntimeStoreError, match="session_busy"):
        begin_cleanup(db, epoch=epoch, root="root")
    assert (
        rt.revert_steer_input(
            db,
            epoch=epoch,
            admission_id=second["admission_id"],
            generation=claim["generation"],
        )
        is True
    )
    assert (
        rt.get_session_admission(db, admission_id=second["admission_id"])["status"]
        == "queued"
    )


def test_unknown_owner_intent_is_not_stolen_by_restarted_epoch(store):
    db, epoch = store
    intent = begin_cleanup(db, epoch=epoch, root="root")
    new_epoch = rt.begin_runtime_epoch(db, instance_id="next")
    with pytest.raises(rt.RuntimeStoreError, match="worktree_cleanup_pending"):
        begin_cleanup(db, epoch=new_epoch, root="root")
    with pytest.raises(rt.RuntimeStoreError, match="stale_epoch"):
        release_refused_cleanup(db, epoch=epoch, root="root", intent=intent)
    with pytest.raises(rt.RuntimeStoreError, match="storage_unavailable"):
        release_refused_cleanup(db, epoch=new_epoch, root="root", intent=intent)
    with pytest.raises(rt.RuntimeStoreError, match="worktree_cleanup_pending"):
        admit(db, new_epoch)


def test_legacy_pending_import_and_retarget_cannot_cross_intent(store, tmp_path):
    db, epoch = store
    begin_cleanup(db, epoch=epoch, root="root")
    frozen = tmp_path / "legacy.db"
    legacy(frozen, [row(1, "root")])
    before = frozen.read_bytes()
    with pytest.raises(rt.RuntimeStoreError, match="worktree_cleanup_pending"):
        rt.import_legacy_session_admissions(
            db,
            epoch=epoch,
            source_path=frozen,
            principal_id="owner",
            writers_drained=True,
        )
    assert frozen.read_bytes() == before
    assert not rt.list_session_admissions(db, session_id="root")
    db.create_session("old", source="test")
    queued = admit(db, epoch, "old")
    with pytest.raises(rt.RuntimeStoreError, match="worktree_cleanup_pending"):
        rt.rehome_queued_inputs(
            db,
            epoch=epoch,
            old_session_id="old",
            new_session_id="root",
            principal_prefix="owner",
        )
    assert (
        rt.get_session_admission(db, admission_id=queued["admission_id"])[
            "target_session_id"
        ]
        == "old"
    )


def test_explicit_fork_keeps_its_independent_admission_scope(store):
    db, epoch = store
    db.create_session(
        "fork",
        source="test",
        parent_session_id="root",
        model_config={"_branched_from": "root"},
    )
    begin_cleanup(db, epoch=epoch, root="root")
    assert admit(db, epoch, "fork")["status"] == "queued"


def test_worker_registration_refuses_intent_and_removed_binding(store):
    db, epoch = store
    intent = begin_cleanup(db, epoch=epoch, root="root")
    args = dict(
        epoch=epoch,
        execution_id="worker",
        session_id="root",
        generation=0,
        kind="compute",
        adoption_secret="fixture",
    )
    with pytest.raises(rt.RuntimeStoreError, match="worktree_cleanup_pending"):
        rt.register_worker_execution(db, **args)
    release_refused_cleanup(db, epoch=epoch, root="root", intent=intent)
    db.mark_conversation_worktree_removed("root")
    with pytest.raises(rt.RuntimeStoreError, match="worktree_removed"):
        rt.register_worker_execution(db, **args)
    with pytest.raises(rt.RuntimeStoreError, match="worktree_removed"):
        admit(db, epoch)


@pytest.mark.parametrize("cleanup_state", ["pending", "removed"])
def test_terminal_worker_registration_replay_preserves_receipt_during_cleanup(store, cleanup_state):
    db, epoch = store
    args = dict(
        epoch=epoch, execution_id="finished-worker", session_id="root", generation=0,
        kind="compute", adoption_secret="fixture", require_idle=True,
    )
    rt.register_worker_execution(db, **args)
    terminal = rt.mutate_worker_execution(
        db, epoch=epoch, execution_id=args["execution_id"], session_id="root", generation=0,
        sequence=1, operation="execution.finish", payload={},
    )
    assert terminal["status"] == "terminal"
    intent = begin_cleanup(db, epoch=epoch, root="root")
    if cleanup_state == "removed":
        release_refused_cleanup(db, epoch=epoch, root="root", intent=intent)
        db.mark_conversation_worktree_removed("root")
    before = dict(db._read_one("SELECT * FROM worker_executions WHERE execution_id=?", (args["execution_id"],)))
    receipt_count = db._read_one("SELECT COUNT(*) FROM worker_receipts")[0]

    replay = rt.register_worker_execution(db, **args)

    assert replay["status"] == "terminal"
    assert "adoption_digest" not in replay
    assert replay == {key: value for key, value in before.items() if key != "adoption_digest"}
    assert dict(db._read_one("SELECT * FROM worker_executions WHERE execution_id=?", (args["execution_id"],))) == before
    assert db._read_one("SELECT COUNT(*) FROM worker_receipts")[0] == receipt_count
    with pytest.raises(rt.RuntimeStoreError, match="worktree_cleanup_pending" if cleanup_state == "pending" else "worktree_removed"):
        rt.register_worker_execution(db, **(args | {"execution_id": "new-worker"}))
    assert db._read_one("SELECT COUNT(*) FROM worker_executions")[0] == 1
    with pytest.raises(rt.RuntimeStoreError, match="worktree_cleanup_pending" if cleanup_state == "pending" else "worktree_removed"):
        rt.adopt_worker_execution(db, **{key: value for key, value in args.items() if key not in {"kind", "require_idle"}})
    for changed in ({"adoption_secret": "different"}, {"kind": "cron"}):
        with pytest.raises(rt.RuntimeStoreError, match="admission_conflict"):
            rt.register_worker_execution(db, **(args | changed))
    with pytest.raises(rt.RuntimeStoreError, match="stale_generation"):
        rt.register_worker_execution(db, **(args | {"generation": 1}))
    with pytest.raises(rt.RuntimeStoreError, match="stale_epoch"):
        rt.register_worker_execution(db, **(args | {"epoch": epoch + 1}))
