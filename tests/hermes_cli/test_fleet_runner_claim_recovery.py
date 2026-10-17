"""Process-crash state-machine tests: inert executor, temp SQLite, no network."""

import importlib.util
import inspect
import json
import sqlite3
from types import SimpleNamespace

import pytest

from hermes_cli.fleet_client import FleetTransientTransportError
from hermes_cli.fleet_runner import FleetRunner
from tests.hermes_cli.test_fleet_claim_recovery import setup_store, request, submit


@pytest.fixture(autouse=True)
def inert(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("real network/process execution forbidden")

    monkeypatch.setattr("socket.socket", refuse)
    monkeypatch.setattr("subprocess.Popen", refuse)


class Coordinator:
    base_url = "http://inert.invalid"

    def __init__(self, store):
        self.store = store
        self.claim_calls = 0
        self.resolve_calls = 0
        self.lose_claim_ack = False
        self.lose_resolve_ack = False

    def __getattr__(self, name):
        return getattr(self.store, name)

    def heartbeat_runner(self, capability, **kwargs):
        return self.store.heartbeat_runner(capability.node_id, capability.profile, **kwargs)

    def claim_request(self, req):
        self.claim_calls += 1
        result = self.store.claim_request(req)
        if self.lose_claim_ack:
            self.lose_claim_ack = False
            raise FleetTransientTransportError("ACK lost")
        return result

    def resolve_claim_request(self, req):
        self.resolve_calls += 1
        result = self.store.resolve_claim_request(req)
        if self.lose_resolve_ack:
            self.lose_resolve_ack = False
            raise FleetTransientTransportError("seal ACK lost")
        return result

    def claim_task(self, *args, **kwargs):
        raise AssertionError("new recovery mode cannot fall back to legacy claim")


def make_runner(tmp_path, coordinator, capability, calls):
    assert "claim_recovery" in inspect.signature(FleetRunner).parameters, (
        "explicit recovery mode missing"
    )

    def execute(argv, body):
        calls.append(body)
        return SimpleNamespace(
            returncode=0, stdout='{"type":"result","text":"done","settled":true}\n', stderr=""
        )

    return FleetRunner(
        "node",
        coordinator,
        "inert-hermes",
        ["profile"],
        [],
        capabilities=[capability],
        state_dir=tmp_path / "runner",
        executor=execute,
        claim_recovery=True,
    )


def journal_type():
    assert importlib.util.find_spec("hermes_cli.fleet_claim_journal"), (
        "durable intent journal missing"
    )
    from hermes_cli.fleet_claim_journal import ClaimJournal

    return ClaimJournal


def test_empty_new_journal_and_delayed_legacy_request_remain_held(tmp_path):
    store, capability, _info = setup_store(tmp_path, cutover=False)
    submit(store, "legacy")
    coordinator = Coordinator(store)
    calls = []
    runner = make_runner(tmp_path, coordinator, capability, calls)
    assert runner.run_once(now=101) is False
    assert coordinator.claim_calls == 0 and calls == []
    assert store.claim_task(capability, now=102) is not None
    runner.close()


def test_claim_ack_loss_restart_recovers_exact_claim_without_second_post(tmp_path):
    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")
    submit(store, "b")
    coordinator = Coordinator(store)
    coordinator.lose_claim_ack = True
    calls = []
    runner = make_runner(tmp_path, coordinator, capability, calls)
    assert runner.run_once(now=101) is False
    assert coordinator.claim_calls == 1 and calls == []
    runner.close()
    restarted = make_runner(tmp_path, coordinator, capability, calls)
    assert restarted.run_once(now=102) is True
    assert coordinator.claim_calls == 1 and calls == ["private body"]
    assert store.list_tasks()[0].status == "completed"
    assert store.list_tasks()[1].status == "pending"
    restarted.close()


def test_before_send_restart_and_resolve_ack_loss_never_claim_or_execute(tmp_path):
    store, capability, info = setup_store(tmp_path)
    cls = journal_type()
    journal = cls(tmp_path / "runner", node_id="node", coordinator=Coordinator.base_url)
    journal.bind(info)
    req = request(info, capability, installation_id=journal.installation_id)
    journal.begin(req)
    journal.close()
    submit(store, "a")
    coordinator = Coordinator(store)
    coordinator.lose_resolve_ack = True
    runner = make_runner(tmp_path, coordinator, capability, [])
    assert runner.run_once(now=102) is False
    assert coordinator.claim_calls == 0 and coordinator.resolve_calls == 1
    runner.close()
    restarted = make_runner(tmp_path, coordinator, capability, [])
    assert restarted.run_once(now=103) is False
    assert coordinator.claim_calls == 0 and coordinator.resolve_calls == 1
    assert store.list_tasks()[0].status == "pending"
    restarted.close()


def test_exclusive_owner_and_corrupt_or_missing_local_journal_fail_closed(tmp_path):
    store, capability, info = setup_store(tmp_path)
    cls = journal_type()
    journal = cls(tmp_path / "runner", node_id="node", coordinator=Coordinator.base_url)
    journal.bind(info)
    with pytest.raises((ValueError, sqlite3.Error)):
        cls(tmp_path / "runner", node_id="node", coordinator=Coordinator.base_url)
    journal.close()
    path = tmp_path / "runner" / "claim-intents.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM journal_meta")
    runner = make_runner(tmp_path, Coordinator(store), capability, [])
    assert runner.run_once(now=101) is False
    assert runner.coordinator.claim_calls == 0
    runner.close()
    path.unlink()
    runner = make_runner(tmp_path, Coordinator(store), capability, [])
    assert runner.run_once(now=101) is False
    assert runner.coordinator.claim_calls == 0
    runner.close()


def test_recovered_expired_cancelled_or_changed_claim_never_executes(tmp_path):
    for state in ("expired", "cancelled", "changed"):
        directory = tmp_path / state
        directory.mkdir()
        store, capability, _info = setup_store(directory)
        submit(store, "a")
        coordinator = Coordinator(store)
        coordinator.lose_claim_ack = True
        calls = []
        runner = make_runner(directory, coordinator, capability, calls)
        assert runner.run_once(now=101) is False
        runner.close()
        if state == "expired":
            store.reclaim_expired(now=200)
        elif state == "cancelled":
            original = store.list_tasks()[0]
            assert store.fail_task(
                "a", original.claim_id, error="settled before cancellation", now=102
            )
            assert store.cancel_task("a", now=102)
        else:
            with store._connection() as conn:
                conn.execute("UPDATE fleet_tasks SET attempt=attempt+1 WHERE task_id='a'")
        restarted = make_runner(directory, coordinator, capability, calls)
        assert restarted.run_once(now=201 if state == "expired" else 103) is False
        assert calls == [] and coordinator.claim_calls == 1
        restarted.close()


def test_executing_durable_state_without_file_never_becomes_prelaunch(tmp_path):
    store, capability, info = setup_store(tmp_path)
    submit(store, "a")
    cls = journal_type()
    journal = cls(tmp_path / "runner", node_id="node", coordinator=Coordinator.base_url)
    journal.bind(info)
    req = request(info, capability, installation_id=journal.installation_id)
    journal.begin(req)
    outcome = store.claim_request(req)
    journal.record(outcome)
    journal.mark_executing(outcome.claim)
    journal.close()
    coordinator = Coordinator(store)
    calls = []
    runner = make_runner(tmp_path, coordinator, capability, calls)
    assert runner.run_once(now=102) is False
    assert calls == [] and coordinator.claim_calls == 0
    runner.close()


def test_local_binding_and_missing_state_dir_cannot_enable_recovery(tmp_path):
    store, _capability, info = setup_store(tmp_path)
    assert "claim_recovery" in inspect.signature(FleetRunner).parameters
    with pytest.raises(ValueError, match="state"):
        FleetRunner("node", Coordinator(store), "inert", ["profile"], [], claim_recovery=True)
    cls = journal_type()
    journal = cls(tmp_path / "runner", node_id="node", coordinator=Coordinator.base_url)
    journal.bind(info)
    journal.close()
    with pytest.raises(ValueError):
        cls(tmp_path / "runner", node_id="other", coordinator=Coordinator.base_url)
    with pytest.raises(ValueError):
        cls(tmp_path / "runner", node_id="node", coordinator="http://different.invalid")


def test_crash_after_claim_journal_commit_before_capture_write_recovers_once(tmp_path, monkeypatch):
    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")
    coordinator = Coordinator(store)
    calls = []
    runner = make_runner(tmp_path, coordinator, capability, calls)
    monkeypatch.setattr(
        runner,
        "_journal_claim",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("crash before capture")),
    )
    assert runner.run_once(now=101) is False
    assert runner._claim_journal.active()["stage"] == "claimed"
    runner.close()
    restarted = make_runner(tmp_path, coordinator, capability, calls)
    assert restarted.run_once(now=102) is True
    assert coordinator.claim_calls == 1 and len(calls) == 1
    restarted.close()


def test_crash_after_executing_commit_without_capture_keeps_restart_fence(tmp_path, monkeypatch):
    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")
    coordinator = Coordinator(store)
    calls = []
    runner = make_runner(tmp_path, coordinator, capability, calls)
    cls = journal_type()
    original = cls.mark_executing

    def crash_after_commit(self, claim):
        original(self, claim)
        raise OSError("crash after execution fence")

    with monkeypatch.context() as patcher:
        patcher.setattr(cls, "mark_executing", crash_after_commit)
        assert runner.run_once(now=101) is False
    assert runner._claim_journal.active()["stage"] == "executing"
    runner.close()
    restarted = make_runner(tmp_path, coordinator, capability, calls)
    assert restarted.run_once(now=102) is False
    assert calls == [] and coordinator.claim_calls == 1
    restarted.close()


def test_completed_delivery_ack_loss_restart_never_reexecutes(tmp_path):
    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")

    class LostCompletion(Coordinator):
        lose = True

        def complete_task(self, *args, **kwargs):
            result = self.store.complete_task(*args, **kwargs)
            if self.lose:
                self.lose = False
                raise FleetTransientTransportError("completion ACK lost")
            return result

    coordinator = LostCompletion(store)
    calls = []
    runner = make_runner(tmp_path, coordinator, capability, calls)
    assert runner.run_once(now=101) is False
    runner.close()
    restarted = make_runner(tmp_path, coordinator, capability, calls)
    assert restarted.run_once(now=102) is False
    assert len(calls) == 1 and coordinator.claim_calls == 1
    assert restarted._claim_journal.active() is None
    restarted.close()


def test_original_admission_evidence_outranks_prelaunch_state(tmp_path, monkeypatch):
    import hashlib

    store, capability, info = setup_store(tmp_path)
    submit(store, "a")
    cls = journal_type()
    journal = cls(tmp_path / "runner", node_id="node", coordinator=Coordinator.base_url)
    journal.bind(info)
    req = request(info, capability, installation_id=journal.installation_id)
    journal.begin(req)
    outcome = store.claim_request(req)
    journal.record(outcome)
    directory = (
        tmp_path
        / "runner"
        / "atomic-claim-captures"
        / hashlib.sha256(outcome.claim["claim_id"].encode()).hexdigest()
    )
    directory.mkdir(parents=True)
    identity = {
        "type": "system",
        "subtype": "admission",
        "session_id": "original-session",
        "admission_id": "original-admission",
    }
    (directory / "stdout-retained").write_text(json.dumps(identity) + "\n")
    journal.close()
    coordinator = Coordinator(store)
    calls, reconciled = [], []
    runner = make_runner(tmp_path, coordinator, capability, calls)

    def reconcile(profile, identity, **kwargs):
        reconciled.append(identity)
        return SimpleNamespace(
            returncode=0,
            stdout='{"type":"result","text":"original done","settled":true}\n',
            stderr="",
        )

    monkeypatch.setattr(runner, "_reconcile", reconcile)
    assert runner.run_once(now=102) is False
    assert calls == [] and coordinator.claim_calls == 0
    assert reconciled == [identity]
    assert store.list_tasks()[0].result == "original done"
    runner.close()


def test_concurrent_poll_cannot_send_second_claim_or_release_owner(tmp_path):
    import threading

    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")
    submit(store, "b")
    coordinator = Coordinator(store)
    calls = []
    runner = make_runner(tmp_path, coordinator, capability, calls)
    entered, release = threading.Event(), threading.Event()

    def execute(argv, body):
        calls.append(body)
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(
            returncode=0, stdout='{"type":"result","text":"done","settled":true}\n', stderr=""
        )

    runner._executor = execute
    results = []
    thread = threading.Thread(target=lambda: results.append(runner.run_once(now=101)))
    thread.start()
    try:
        assert entered.wait(3)
        assert runner.run_once(now=101) is False
        with pytest.raises(RuntimeError):
            runner.close()
        assert coordinator.claim_calls == 1
    finally:
        release.set()
        thread.join(3)
    assert results == [True] and len(calls) == 1
    runner.close()


def test_old_or_changed_coordinator_cannot_enable_or_clear_pending_intent(tmp_path, monkeypatch):
    store, capability, info = setup_store(tmp_path)
    submit(store, "a")
    coordinator = Coordinator(store)
    coordinator.lose_claim_ack = True
    runner = make_runner(tmp_path, coordinator, capability, [])
    assert runner.run_once(now=101) is False
    for malformed in ({"ok": True}, {**info, "store_id": "f" * 64}, {**info, "cutover": None}):
        with monkeypatch.context() as patcher:
            patcher.setattr(coordinator, "claim_recovery_capabilities", lambda: malformed)
            assert runner.run_once(now=102) is False
            assert coordinator.claim_calls == 1
            assert runner._claim_journal.active()["stage"] == "intent"
    runner.close()


def test_downgrading_mode_cannot_bypass_a_persisted_unacknowledged_intent(tmp_path):
    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")
    coordinator = Coordinator(store)
    coordinator.lose_claim_ack = True
    first = make_runner(tmp_path, coordinator, capability, [])
    assert first.run_once(now=101) is False
    first.close()
    downgraded = FleetRunner(
        "node",
        coordinator,
        "inert",
        ["profile"],
        [],
        capabilities=[capability],
        state_dir=tmp_path / "runner",
        executor=lambda *_: pytest.fail("no replay"),
    )
    assert downgraded.run_once(now=102) is False
    assert coordinator.claim_calls == 1
    downgraded.close()


def test_unreconciled_outer_legacy_capture_holds_new_mode(tmp_path):
    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")
    directory = tmp_path / "runner" / "legacy-capture"
    directory.mkdir(parents=True)
    (directory / "claim.json").write_text('{"claim_id":"legacy"}')
    coordinator = Coordinator(store)
    runner = make_runner(tmp_path, coordinator, capability, [])
    assert runner.run_once(now=101) is False
    assert coordinator.claim_calls == 0
    assert (directory / "claim.json").exists()
    runner.close()


def test_new_capture_namespace_is_invisible_to_unmodified_legacy_globs(tmp_path):
    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")
    coordinator = Coordinator(store)
    runner = make_runner(tmp_path, coordinator, capability, [])
    runner._executor = lambda *_: (_ for _ in ()).throw(OSError("crash after execution fence"))
    assert runner.run_once(now=101) is False
    assert not list((tmp_path / "runner").glob("*/claim.json"))
    assert list((tmp_path / "runner" / "atomic-claim-captures").glob("*/claim.json"))
    runner.close()


@pytest.mark.parametrize("race", ["expiry", "terminal", "identity"])
def test_current_claim_change_between_lookup_and_renewal_cannot_admit_execution(
    tmp_path, monkeypatch, race
):
    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")
    coordinator = Coordinator(store)
    coordinator.lose_claim_ack = True
    calls = []
    runner = make_runner(tmp_path, coordinator, capability, calls)
    assert runner.run_once(now=101) is False
    runner.close()

    def renew(task_id, claim_id, **kwargs):
        if race == "expiry":
            assert store.reclaim_expired(now=200) == 1
        elif race == "terminal":
            assert store.fail_task(task_id, claim_id, error="settled", now=102)
            assert store.cancel_task(task_id, now=102)
        else:
            with store._connection() as conn:
                conn.execute("UPDATE fleet_tasks SET attempt=attempt+1 WHERE task_id=?", (task_id,))
        return store.renew_claim(task_id, claim_id, **kwargs)

    monkeypatch.setattr(coordinator, "renew_claim", renew)
    restarted = make_runner(tmp_path, coordinator, capability, calls)
    assert restarted.run_once(now=102) is False
    assert calls == [] and coordinator.claim_calls == 1
    restarted.close()


def test_durable_intent_precedes_send_even_when_transport_never_reaches_store(
    tmp_path, monkeypatch
):
    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")
    coordinator = Coordinator(store)
    calls = []
    runner = make_runner(tmp_path, coordinator, capability, calls)

    def lose_before_send(req):
        active = runner._claim_journal.active()
        assert active["stage"] == "intent" and active["request"] == req
        assert store.lookup_claim_request(req).kind == "absent"
        coordinator.claim_calls += 1
        raise FleetTransientTransportError("request lost before store transaction")

    monkeypatch.setattr(coordinator, "claim_request", lose_before_send)
    assert runner.run_once(now=101) is False
    runner.close()
    restarted = make_runner(tmp_path, coordinator, capability, calls)
    assert restarted.run_once(now=102) is False
    assert calls == [] and coordinator.claim_calls == 1 and coordinator.resolve_calls == 1
    assert store.list_tasks()[0].attempt == 0
    restarted.close()


def test_second_renewal_identity_mismatch_cannot_cross_execution_admission(tmp_path, monkeypatch):
    from dataclasses import replace

    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")
    coordinator = Coordinator(store)
    calls = []
    runner = make_runner(tmp_path, coordinator, capability, calls)
    renewals = []

    def renew(task_id, claim_id, **kwargs):
        renewed = store.renew_claim(task_id, claim_id, **kwargs)
        renewals.append(renewed)
        return replace(renewed, attempt=renewed.attempt + 1) if len(renewals) == 2 else renewed

    monkeypatch.setattr(coordinator, "renew_claim", renew)
    assert runner.run_once(now=101) is False
    assert calls == [] and coordinator.claim_calls == 1
    runner.close()


def test_stop_after_durable_execution_fence_but_before_executor_never_launches(
    tmp_path, monkeypatch
):
    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")
    coordinator = Coordinator(store)
    calls = []
    runner = make_runner(tmp_path, coordinator, capability, calls)
    cls = journal_type()
    original = cls.mark_executing

    def stop_after_fence(self, claim):
        original(self, claim)
        runner.stop()

    monkeypatch.setattr(cls, "mark_executing", stop_after_fence)
    runner.run_once(now=101)
    assert calls == []
    runner.close()


@pytest.mark.parametrize("pending_delivery", [False, True])
@pytest.mark.parametrize("fault", ["finish", "_remove_capture"])
def test_acknowledged_settlement_commits_before_proof_cleanup(
    tmp_path, monkeypatch, pending_delivery, fault
):
    store, capability, _info = setup_store(tmp_path)
    submit(store, "a")
    submit(store, "b")
    coordinator = Coordinator(store)
    calls = []
    runner = make_runner(tmp_path, coordinator, capability, calls)
    if pending_delivery:
        with monkeypatch.context() as patch:

            def lost_ack(*args, **kwargs):
                store.complete_task(*args, **kwargs)
                raise FleetTransientTransportError("completion ACK lost")

            patch.setattr(coordinator, "complete_task", lost_ack)
            assert runner.run_once(now=101) is False

    def crash(*args, **kwargs):
        raise OSError("inert settlement fault")

    with monkeypatch.context() as patch:
        owner = journal_type() if fault == "finish" else FleetRunner
        patch.setattr(owner, fault, crash)
        assert runner.run_once(now=102 if pending_delivery else 101) is False
    assert [task.status for task in store.list_tasks()] == ["completed", "pending"]
    assert len(calls) == 1 and coordinator.claim_calls == 1
    active = runner._claim_journal.active()
    if fault == "finish":
        assert active["stage"] == "executing"
        assert len(list(runner._capture_root().glob("*/claim.json"))) == 1
    else:
        assert active is None
    assert len(list(runner._capture_root().glob("*.delivery.json"))) == 1
    runner.close()

    restarted = make_runner(tmp_path, coordinator, capability, calls)
    for now in (103, 104):
        restarted.run_once(now=now)
        if len(calls) == 2:
            break
    assert [task.status for task in store.list_tasks()] == ["completed", "completed"]
    assert len(calls) == 2 and coordinator.claim_calls == 2
    assert restarted._claim_journal.active() is None
    assert list(restarted._capture_root().glob("*/claim.json")) == []
    assert list(restarted._capture_root().glob("*.delivery.json")) == []
    restarted.close()
