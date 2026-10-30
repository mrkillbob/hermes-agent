"""Durable accounting using private temporary databases and synthetic approvals."""
from dataclasses import replace
import multiprocessing

import pytest

from agent.execution_artifact_contracts import ArtifactDenied
from agent.execution_artifact_ledger import DurableArtifactAuthority
from agent.execution_artifacts import ExecutionArtifactRegistry
from tests.agent.test_execution_artifacts import IDENTITY, INVOCATION, RECIPIENT, Authority, Executor, Sink


def test_disabled_ledger_does_not_touch_filesystem(tmp_path):
    before = set(tmp_path.iterdir())
    with pytest.raises(ArtifactDenied, match="disabled"):
        DurableArtifactAuthority(tmp_path / "state.db")
    assert set(tmp_path.iterdir()) == before


@pytest.mark.platforms("posix")
def test_exact_pipeline_survives_reopen_without_replay(tmp_path):
    path = tmp_path / "state.db"
    authority = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    approval = Authority().approval
    authority.record_approval(approval)
    registry = ExecutionArtifactRegistry(enabled=True, authority=authority, executor=Executor(), clock=lambda: 100.)
    ref = registry.produce(IDENTITY, INVOCATION, RECIPIENT, request_id="request")
    sink = Sink()
    registry.dispatch(ref, identity=IDENTITY, invocation=INVOCATION, request_id="request", sink=sink)
    assert len(sink.sent) == 1
    reopened = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    reopened.record_approval(approval)
    with pytest.raises(ArtifactDenied):
        reopened.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    with pytest.raises(ArtifactDenied):
        with reopened.dispatch_scope(approval, 10):
            pytest.fail("consumed release admitted")


@pytest.mark.platforms("posix")
def test_uncertain_execution_and_send_remain_consumed_on_restart(tmp_path):
    path = tmp_path / "state.db"
    authority = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    approval = Authority().approval
    authority.record_approval(approval)
    authority.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    reopened = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    with pytest.raises(ArtifactDenied):
        reopened.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    with pytest.raises(OSError):
        with reopened.dispatch_scope(approval, 10):
            raise OSError("synthetic lost acknowledgment")
    with pytest.raises(ArtifactDenied):
        with DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.).dispatch_scope(approval, 10):
            pytest.fail("uncertain send replayed")


@pytest.mark.platforms("posix")
def test_revocation_and_scope_changes_are_denied(tmp_path):
    authority = DurableArtifactAuthority(tmp_path / "state.db", enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    approval = Authority().approval
    authority.record_approval(approval)
    with pytest.raises(ArtifactDenied):
        authority.approve(replace(IDENTITY, session="foreign"), INVOCATION, RECIPIENT, "request")
    authority.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    authority.revoke(approval.approval_id)
    assert not authority.current(approval)
    with pytest.raises(ArtifactDenied):
        with authority.dispatch_scope(approval, 10):
            pytest.fail("revoked release sent")


@pytest.mark.platforms("posix")
def test_task_budget_is_durable_across_admissions_and_approval_ids(tmp_path):
    path = tmp_path / "state.db"
    authority = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    approval = replace(Authority().approval, request_budget=1)
    authority.record_approval(approval)
    authority.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    with authority.dispatch_scope(approval, 10):
        pass
    refreshed = replace(approval, approval_id="approval-2", invocation_id="invocation-2",
                        request_id="request-2", identity=replace(IDENTITY, admission="new-admission"))
    reopened = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    reopened.record_approval(refreshed)
    reopened.approve(refreshed.identity, INVOCATION, RECIPIENT, "request-2")
    with pytest.raises(ArtifactDenied, match="task_budget"):
        with reopened.dispatch_scope(refreshed, 10):
            pytest.fail("task budget reset")


@pytest.mark.platforms("posix")
def test_immutable_record_and_private_directory_required(tmp_path):
    authority = DurableArtifactAuthority(tmp_path / "state.db", enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    approval = Authority().approval
    authority.record_approval(approval)
    with pytest.raises(ArtifactDenied, match="immutable"):
        authority.record_approval(replace(approval, recipient=replace(RECIPIENT, account="foreign")))
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(ArtifactDenied, match="private"):
        DurableArtifactAuthority(public / "state.db", enabled=True, admission_check=lambda _: True)
    alias = tmp_path / "alias.db"
    alias.symlink_to(tmp_path / "state.db")
    with pytest.raises(OSError):
        DurableArtifactAuthority(alias, enabled=True, admission_check=lambda _: True)


@pytest.mark.platforms("posix")
def test_invocation_cannot_be_reassigned_after_uncertain_execution(tmp_path):
    path = tmp_path / "state.db"
    authority = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    approval = Authority().approval
    authority.record_approval(approval)
    authority.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    reopened = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    reassigned = replace(approval, approval_id="another-approval", request_id="another-request")
    reopened.record_approval(reassigned)
    with pytest.raises(ArtifactDenied, match="execution_or_request_spent"):
        reopened.approve(IDENTITY, INVOCATION, RECIPIENT, "another-request")


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("delivered", [False, True])
def test_new_ids_cannot_reexecute_same_request(tmp_path, delivered):
    path = tmp_path / "state.db"
    ledger = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    approval = Authority().approval
    ledger.record_approval(approval)
    ledger.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    if delivered:
        with ledger.dispatch_scope(approval, 10):
            pass
    reopened = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    fresh = replace(approval, approval_id="new-approval", invocation_id="new-invocation")
    reopened.record_approval(fresh)
    with pytest.raises(ArtifactDenied, match="execution_or_request_spent"):
        reopened.approve(IDENTITY, INVOCATION, RECIPIENT, "request")


@pytest.mark.platforms("posix")
def test_live_admission_verification_is_required_at_each_release(tmp_path):
    state = {"active": True}
    ledger = DurableArtifactAuthority(tmp_path / "state.db", enabled=True,
                                     admission_check=lambda _: state["active"], clock=lambda: 100.)
    approval = Authority().approval
    ledger.record_approval(approval)
    ledger.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    state["active"] = False
    assert not ledger.current(approval)
    with pytest.raises(ArtifactDenied):
        with ledger.dispatch_scope(approval, 10):
            pytest.fail("stale admission sent")


def _budget_child(path, approval, ready, results):
    ledger = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    ledger.approve(approval.identity, INVOCATION, RECIPIENT, approval.request_id)
    ready.wait(timeout=20)
    try:
        with ledger.dispatch_scope(approval, 10):
            results.put("sent")
    except ArtifactDenied:
        results.put("denied")


@pytest.mark.platforms("posix")
def test_cross_process_task_budget_has_one_winner(tmp_path):
    path = tmp_path / "state.db"
    ledger = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    first = replace(Authority().approval, request_budget=1)
    second = replace(first, approval_id="second", invocation_id="second", request_id="second")
    for approval in (first, second):
        ledger.record_approval(approval)
    context = multiprocessing.get_context("spawn")
    ready, results = context.Barrier(2), context.Queue()
    children = [context.Process(target=_budget_child, args=(path, a, ready, results)) for a in (first, second)]
    try:
        for child in children:
            child.start()
        outcomes = [results.get(timeout=30), results.get(timeout=30)]
        for child in children:
            child.join(timeout=30)
            assert child.exitcode == 0
        assert sorted(outcomes) == ["denied", "sent"]
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(timeout=10)
        results.close()


def _held_dispatch_child(path, approval, entered, release, order):
    ledger = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    with ledger.dispatch_scope(approval, 10):
        with open(order, "a") as handle:
            handle.write("send\n")
        entered.set()
        if not release.wait(timeout=20):
            raise RuntimeError("synthetic release not signaled")
        with open(order, "a") as handle:
            handle.write("settled\n")


def _revoke_child(path, approval_id, ready, begin, attempted, order):
    ledger = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    ready.set()
    if not begin.wait(timeout=20):
        raise RuntimeError("synthetic revoke not signaled")
    attempted.set()
    ledger.revoke(approval_id)
    with open(order, "a") as handle:
        handle.write("revoked\n")


@pytest.mark.platforms("posix")
def test_cross_process_revoke_serializes_after_inflight_send(tmp_path):
    path, order = tmp_path / "state.db", tmp_path / "order.txt"
    ledger = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    approval = Authority().approval
    ledger.record_approval(approval)
    ledger.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    context = multiprocessing.get_context("spawn")
    entered, release, attempted = context.Event(), context.Event(), context.Event()
    ready, begin = context.Event(), context.Event()
    sender = context.Process(target=_held_dispatch_child, args=(path, approval, entered, release, order))
    revoker = context.Process(target=_revoke_child, args=(path, approval.approval_id, ready, begin, attempted, order))
    children = [sender, revoker]
    try:
        revoker.start()
        assert ready.wait(timeout=30)
        sender.start()
        assert entered.wait(timeout=30)
        begin.set()
        assert attempted.wait(timeout=30)
        release.set()
        for child in children:
            child.join(timeout=30)
            assert child.exitcode == 0
        assert order.read_text().splitlines() == ["send", "settled", "revoked"]
        assert not ledger.current(approval)
    finally:
        begin.set()
        release.set()
        for child in children:
            if child.pid is not None and child.is_alive():
                child.terminate()
                child.join(timeout=10)


@pytest.mark.platforms("windows")
def test_unqualified_windows_acl_is_denied_before_file_creation(tmp_path):
    with pytest.raises(ArtifactDenied, match="windows_acl_unqualified"):
        DurableArtifactAuthority(tmp_path / "state.db", enabled=True, admission_check=lambda _: True)
    assert not (tmp_path / "state.db").exists()
