"""Atomic production-store recovery contracts; temporary SQLite, no transports."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_cli import fleet_protocol as protocol
from hermes_cli.fleet_protocol import FleetTask, RunnerCapability, TaskRequirement
from hermes_cli.fleet_store import FleetStore


@pytest.fixture(autouse=True)
def inert(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("live transport or executor forbidden")

    monkeypatch.setattr("socket.socket", refuse)
    monkeypatch.setattr("subprocess.Popen", refuse)


def setup_store(tmp_path, *, cutover=True):
    store = FleetStore(tmp_path / "fleet.db")
    capability = RunnerCapability("node", "profile", platform="posix")
    store.register_runner(capability, now=100, ttl=1000)
    assert hasattr(store, "claim_recovery_capabilities"), "new protocol must be explicit"
    info = store.claim_recovery_capabilities()
    if cutover:
        store.establish_claim_cutover(info["store_id"])
        info = store.claim_recovery_capabilities()
    return store, capability, info


def request(info, capability, *, nonce="a" * 64, **changes):
    cls = getattr(protocol, "ClaimRequest", None)
    assert cls is not None, "strict request protocol missing"
    return cls.from_dict(
        {
            "version": "hermes.fleet.claim-recovery.v1",
            "store_id": info["store_id"],
            "cutover_id": info["cutover"]["cutover_id"],
            "installation_id": "b" * 64,
            "nonce": nonce,
            "capability": capability.to_dict(),
            "now": 101.0,
            "lease_seconds": 60.0,
            **changes,
        }
    )


def submit(store, name):
    return store.submit_task(
        FleetTask(name, name, "private body", TaskRequirement(), name), now=100
    )


def test_claim_ack_loss_returns_exact_immutable_outcome_once(tmp_path):
    store, capability, info = setup_store(tmp_path)
    submit(store, "a")
    submit(store, "b")
    req = request(info, capability)
    first = store.claim_request(req)
    second = store.claim_request(req)
    assert first == second == store.lookup_claim_request(req)
    assert first.kind == "claimed" and first.claim["task_id"] == "a"
    assert store.list_tasks()[0].attempt == 1
    assert store.list_tasks()[1].status == "pending"
    assert store.list_runners()[0]["active_load"] == 1
    with store._connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM fleet_events WHERE event_type='task_claimed'"
            ).fetchone()[0]
            == 1
        )
        assert (
            "private body"
            not in conn.execute("SELECT outcome_json FROM fleet_claim_requests").fetchone()[0]
        )
    restarted = FleetStore(tmp_path / "fleet.db")
    assert restarted.lookup_claim_request(req) == first


def test_empty_and_sealed_outcomes_never_select_later_work(tmp_path):
    store, capability, info = setup_store(tmp_path)
    empty = request(info, capability)
    assert store.claim_request(empty).kind == "empty"
    absent = request(info, capability, nonce="c" * 64)
    assert store.lookup_claim_request(absent).kind == "absent"
    sealed = store.resolve_claim_request(absent)
    assert sealed.kind == "sealed_empty"
    submit(store, "later")
    assert store.claim_request(empty).kind == "empty"
    assert store.claim_request(absent) == sealed == store.resolve_claim_request(absent)
    assert store.list_tasks()[0].attempt == 0


def test_claim_and_seal_serialize_both_orderings_and_concurrent_requests(tmp_path):
    store, capability, info = setup_store(tmp_path)
    submit(store, "a")
    req = request(info, capability)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(lambda f: f(req), (store.claim_request, store.resolve_claim_request))
        )
    assert outcomes[0] == outcomes[1]
    assert outcomes[0].kind in {"claimed", "sealed_empty"}
    assert store.list_tasks()[0].attempt == (outcomes[0].kind == "claimed")


def test_binding_collision_and_store_or_cutover_mismatch_hold(tmp_path):
    store, capability, info = setup_store(tmp_path)
    req = request(info, capability)
    store.resolve_claim_request(req)
    for changes in (
        {"lease_seconds": 30},
        {"installation_id": "c" * 64},
        {"store_id": "d" * 64},
        {"cutover_id": "e" * 64},
    ):
        changed = request(info, capability, **changes)
        for method in (
            store.claim_request,
            store.lookup_claim_request,
            store.resolve_claim_request,
        ):
            with pytest.raises(ValueError):
                method(changed)


def test_empty_new_state_is_not_cutover_and_legacy_commit_blocks_cutover(tmp_path):
    store, capability, info = setup_store(tmp_path, cutover=False)
    assert info["cutover"] is None
    submit(store, "a")
    store.claim_task(capability, now=101)
    with pytest.raises(ValueError, match="quiescent"):
        store.establish_claim_cutover(info["store_id"])
    assert store.claim_recovery_capabilities()["cutover"] is None


def test_cutover_first_fences_delayed_old_sql_and_stale_nonce_after_retry(tmp_path):
    store, capability, info = setup_store(tmp_path)
    submit(store, "a")

    # Exercise the old transaction SQL, not merely a new Python guard.
    def legacy_update():
        with store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE fleet_tasks SET status='running', claim_id='legacy', node_id='node', runner_profile='profile', attempt=attempt+1 WHERE task_id='a' AND status='pending'"
            )
            conn.execute("UPDATE fleet_runners SET active_load=active_load+1")
            store._event(conn, "a", "task_claimed", 101)

    with pytest.raises(sqlite3.IntegrityError):
        legacy_update()
    assert store.list_tasks()[0].attempt == 0
    assert store.list_runners()[0]["active_load"] == 0
    claim = store.claim_request(request(info, capability)).claim
    assert store.fail_task("a", claim["claim_id"], error="settled", now=102)
    assert store.retry_task("a", now=103)
    with pytest.raises(sqlite3.IntegrityError):
        legacy_update()
    assert store.list_tasks()[0].attempt == 1


def test_cutover_refuses_uncertain_and_nonzero_load_without_releasing_work(tmp_path):
    store, capability, info = setup_store(tmp_path, cutover=False)
    submit(store, "a")
    claim = store.claim_task(capability, now=101)
    assert store.mark_uncertain("a", claim.claim_id, error="lost", now=102)
    with pytest.raises(ValueError):
        store.establish_claim_cutover(info["store_id"])
    assert store.list_tasks()[0].status == "uncertain"


def test_protocol_rejects_legacy_malformed_unknown_and_missing_fields(tmp_path):
    _store, capability, info = setup_store(tmp_path)
    req = request(info, capability)
    for changes in (
        {"nonce": "short"},
        {"lease_seconds": True},
        {"now": float("nan")},
        {"version": "legacy"},
    ):
        with pytest.raises(ValueError):
            request(info, capability, **changes)
    for value in ({}, {"claim": None}, {"kind": "empty"}, {"kind": "unknown"}):
        with pytest.raises(ValueError):
            protocol.ClaimOutcome.from_dict(value, request=req)


def test_another_installation_cannot_adopt_same_node_after_local_state_loss(tmp_path):
    store, capability, info = setup_store(tmp_path)
    submit(store, "a")
    store.claim_request(request(info, capability))
    replacement = request(info, capability, nonce="c" * 64, installation_id="d" * 64)
    for method in (store.claim_request, store.resolve_claim_request, store.lookup_claim_request):
        with pytest.raises(ValueError, match="installation"):
            method(replacement)


def test_final_ledger_failure_rolls_back_claim_attempt_load_events_and_reservation(tmp_path):
    store, capability, info = setup_store(tmp_path)
    submit(store, "a")
    with store._connection() as conn:
        conn.execute(
            "CREATE TRIGGER reject_fixture_outcome BEFORE UPDATE ON fleet_claim_requests WHEN NEW.outcome_json IS NOT NULL BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.claim_request(request(info, capability))
    assert store.list_tasks()[0].status == "pending"
    assert store.list_tasks()[0].attempt == 0
    assert store.list_runners()[0]["active_load"] == 0
    with store._connection() as conn:
        assert conn.execute("SELECT count(*) FROM fleet_claim_requests").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM fleet_events WHERE event_type='task_claimed'"
            ).fetchone()[0]
            == 0
        )


def test_outcome_tombstone_cannot_be_pruned_or_rewritten_and_lookup_is_read_only(tmp_path):
    store, capability, info = setup_store(tmp_path)
    req = request(info, capability)
    assert store.lookup_claim_request(req).kind == "absent"
    with store._connection() as conn:
        assert conn.execute("SELECT count(*) FROM fleet_claim_requests").fetchone()[0] == 0
    sealed = store.resolve_claim_request(req)
    for sql in (
        "DELETE FROM fleet_claim_requests",
        "UPDATE fleet_claim_requests SET outcome_json=NULL",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            with store._connection() as conn:
                conn.execute(sql)
    assert store.resolve_claim_request(req) == sealed
    submit(store, "a")
    assert store.claim_request(req) == sealed


def test_cutover_rejects_nonzero_runner_load_and_meta_corruption(tmp_path):
    store, _capability, info = setup_store(tmp_path, cutover=False)
    with store._connection() as conn:
        conn.execute("UPDATE fleet_runners SET active_load=1")
    with pytest.raises(ValueError, match="quiescent"):
        store.establish_claim_cutover(info["store_id"])
    with store._connection() as conn:
        conn.execute("DROP TRIGGER fleet_recovery_meta_update")
        conn.execute("UPDATE fleet_recovery_meta SET store_id='invalid'")
    with pytest.raises(ValueError):
        store.claim_recovery_capabilities()


def test_unchanged_legacy_store_constructor_and_claim_obey_persistent_cutover(tmp_path):
    import importlib.util
    from pathlib import Path
    import sys

    # Load the exact committed baseline as a separate module, retaining the
    # shared protocol classes. This exercises old constructor/transaction code.
    name = "hermes_cli._fleet_store_13f_qualification"
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parent / "fixtures" / "fleet_store_13f.py"
    )
    legacy_module = importlib.util.module_from_spec(spec)
    sys.modules[name] = legacy_module
    spec.loader.exec_module(legacy_module)
    store, capability, info = setup_store(tmp_path)
    submit(store, "a")
    legacy = legacy_module.FleetStore(tmp_path / "fleet.db")
    with pytest.raises(sqlite3.IntegrityError, match="legacy claims permanently closed"):
        legacy.claim_task(capability, now=101)
    assert store.list_tasks()[0].attempt == 0
    assert store.list_runners()[0]["active_load"] == 0
    with store._connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM fleet_events WHERE event_type='task_claimed'"
            ).fetchone()[0]
            == 0
        )
    # The same old class winning first leaves real work and prevents cutover.
    other = tmp_path / "legacy-first"
    other.mkdir()
    waiting, capability, info = setup_store(other, cutover=False)
    submit(waiting, "a")
    old_first = legacy_module.FleetStore(other / "fleet.db")
    assert old_first.claim_task(capability, now=101) is not None
    with pytest.raises(ValueError, match="quiescent"):
        waiting.establish_claim_cutover(info["store_id"])
    assert waiting.list_tasks()[0].status == "running"
