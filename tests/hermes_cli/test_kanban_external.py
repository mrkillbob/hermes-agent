"""External effects are attempted once; only bound terminal evidence closes a run."""
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as dispatch


@pytest.fixture
def conn(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    kb.init_db()
    with kbc.connect_closing() as connection:
        yield connection


def api():
    from hermes_cli import kanban_external
    return kanban_external


class Executor:
    """Inert receiver with an observable irreversible submission counter."""
    def __init__(self, behavior='ack'):
        self.behavior = behavior
        self.deliveries = []
        self.receipt = None
        self.lookups = []

    def submit(self, delivery):
        self.deliveries.append(delivery)
        if self.behavior == 'lost_ack':
            raise TimeoutError('secret exception detail must not be journaled')
        self.receipt = api().Artifact(delivery, 'ack', 'receiver accepted')
        return self.receipt

    def lookup(self, delivery):
        self.lookups.append(delivery)
        return self.receipt


def start(conn, executor=None):
    worker = executor or Executor()
    lanes = api().ExternalLanes({'external-fixture': api().Lane('fixture-local', worker)})
    tid = kb.create_task(conn, title='synthetic work', body='only this input', assignee='external-fixture')
    result = dispatch.dispatch_once(conn, task_ids=[tid], spawn_fn=lanes)
    assert [row[0] for row in result.spawned] == [tid]
    return tid, lanes, worker, worker.deliveries[0]


def test_explicit_registry_dispatches_once_and_ack_is_not_completion(conn):
    tid, lanes, worker, delivery = start(conn)
    assert kb.get_task(conn, tid).status == 'running'
    assert kb.get_task(conn, tid).worker_pid is None
    assert kb.get_run(conn, delivery.run_id).ended_at is None
    lanes.deliver(conn, kb.get_task(conn, tid), delivery.workspace)
    dispatch.dispatch_once(conn, task_ids=[tid], spawn_fn=lanes)
    assert len(worker.deliveries) == 1
    assert [e.kind for e in kb.list_events(conn, tid)].count('external_intent') == 1


def test_lost_ack_and_restart_never_resubmit_and_lookup_can_finish(conn):
    tid, _lanes, worker, delivery = start(conn, Executor('lost_ack'))
    assert kb.get_task(conn, tid).status == 'running'
    assert 'secret exception' not in str(kb.list_events(conn, tid))
    restarted = api().ExternalLanes({'external-fixture': api().Lane('fixture-local', worker)})
    restarted.deliver(conn, kb.get_task(conn, tid), delivery.workspace)
    assert restarted.reconcile(conn, delivery) is False
    worker.receipt = api().Artifact(delivery, 'completed', 'checked synthetic output', findings=('fixture evidence',))
    assert restarted.reconcile(conn, delivery) is True
    assert kb.get_task(conn, tid).status == 'done'
    assert len(worker.deliveries) == 1
    assert worker.lookups == [delivery, delivery]


@pytest.mark.parametrize('kind', ['accepted', 'ack', 'started'])
def test_nonterminal_receipts_never_close_run(conn, kind):
    tid, lanes, _, delivery = start(conn)
    lanes.receive(conn, api().Artifact(delivery, kind, 'receiver accepted'))
    assert kb.get_task(conn, tid).status == 'running'
    assert kb.get_run(conn, delivery.run_id).outcome is None


@pytest.mark.parametrize('kind', ['question', 'blocked', 'error'])
def test_terminal_noncompletion_is_sticky_block_with_typed_evidence(conn, kind):
    tid, lanes, _, delivery = start(conn)
    artifact = api().Artifact(delivery, kind, 'owner attention required', questions=('which input?',))
    assert lanes.receive(conn, artifact)
    assert kb.get_task(conn, tid).status == 'blocked'
    assert kb.get_task(conn, tid).block_kind == 'needs_input'
    assert kb.get_run(conn, delivery.run_id).outcome == 'blocked'
    assert lanes.receive(conn, artifact)
    assert len([e for e in kb.list_events(conn, tid) if e.kind == 'external_terminal']) == 1


def test_terminal_receipt_reconciles_crash_before_and_after_lifecycle(conn, monkeypatch):
    tid, lanes, worker, delivery = start(conn)
    terminal = api().Artifact(delivery, 'completed', 'evidence retained')
    complete = kb.complete_task
    def crash(*args, **kwargs):
        raise RuntimeError('crash after receipt commit')
    monkeypatch.setattr(kb, 'complete_task', crash)
    with pytest.raises(RuntimeError):
        lanes.receive(conn, terminal)
    assert kb.get_task(conn, tid).status == 'running'
    assert any(e.kind == 'external_terminal' for e in kb.list_events(conn, tid))
    monkeypatch.setattr(kb, 'complete_task', complete)
    worker.receipt = None
    assert lanes.reconcile(conn, delivery)
    assert lanes.receive(conn, terminal)
    assert len([e for e in kb.list_events(conn, tid) if e.kind == 'completed']) == 1
    with pytest.raises(ValueError):
        lanes.receive(conn, api().Artifact(delivery, 'error', 'conflicting verdict'))


@pytest.mark.parametrize('field,value', [('task_id', 't_foreign'), ('run_id', 999999),
    ('claim', 'foreign-owner'), ('executor', 'foreign-executor'), ('database', '/foreign/board'),
    ('key', 'different-key'), ('payload_sha256', '0' * 64), ('lane', 'external-foreign')])
def test_foreign_binding_cannot_close_task(conn, field, value):
    tid, lanes, _, delivery = start(conn)
    with pytest.raises(ValueError):
        forged = replace(delivery, **{field: value})
        lanes.receive(conn, api().Artifact(forged, 'completed', 'foreign result'))
    assert kb.get_task(conn, tid).status == 'running'


def test_stale_claim_and_reassignment_refuse_receipts(conn):
    tid, lanes, _, delivery = start(conn)
    conn.execute('UPDATE tasks SET claim_lock = ? WHERE id = ?', ('successor', tid))
    conn.commit()
    with pytest.raises(ValueError):
        lanes.receive(conn, api().Artifact(delivery, 'completed', 'stale result'))
    assert kb.get_task(conn, tid).status == 'running'


def test_unresolved_intent_fences_ttl_stale_runtime_orphan_and_new_run(conn):
    tid, lanes, worker, _delivery = start(conn, Executor('lost_ack'))
    conn.execute('UPDATE tasks SET claim_expires=1, last_heartbeat_at=1, started_at=1, max_runtime_seconds=1 WHERE id=?', (tid,))
    conn.commit()
    assert kb.release_stale_claims(conn) == 0
    assert dispatch.detect_stale_running(conn, stale_timeout_seconds=1) == []
    assert dispatch.enforce_max_runtime(conn) == []
    conn.execute('UPDATE tasks SET claim_expires=NULL WHERE id=?', (tid,))
    conn.commit()
    assert dispatch.reconcile_orphaned_running(conn) == []
    # Even an explicit operator reclaim does not mean the old external effects stopped.
    assert kb.reclaim_task(conn, tid)
    dispatch.dispatch_once(conn, task_ids=[tid], spawn_fn=lanes)
    assert len(worker.deliveries) == 1


def test_unknown_lane_and_real_profile_keep_existing_dispatch_rules(conn, monkeypatch):
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, 'profile_exists', lambda name: name == 'default')
    seen = []
    lanes = api().ExternalLanes({}, profile_spawn=lambda task, workspace, board=None: seen.append(task.id))
    unknown = kb.create_task(conn, title='unknown', assignee='external-missing')
    normal = kb.create_task(conn, title='normal', assignee='default')
    result = dispatch.dispatch_once(conn, task_ids=[unknown, normal], spawn_fn=lanes)
    assert result.skipped_nonspawnable == [unknown]
    assert seen == [normal]
    assert kb.get_task(conn, unknown).status == 'ready'


def test_registry_and_artifact_validation_are_closed(conn):
    with pytest.raises(ValueError):
        api().ExternalLanes({'default': api().Lane('fixture', Executor())})
    _, _, _, delivery = start(conn)
    for kind, text in [('done', 'bad state'), ('completed', ''), ('error', 'x' * 65537)]:
        with pytest.raises(ValueError):
            api().Artifact(delivery, kind, text)


def test_wire_roundtrip_rejects_tampered_digest_unknown_fields_and_wrong_types(conn):
    import json
    from dataclasses import asdict
    _, _, _, delivery = start(conn)
    artifact = api().Artifact(delivery, 'completed', 'evidence', findings=('one',))
    wire = json.loads(json.dumps({'artifact': asdict(artifact), 'sha256': artifact.sha256}))
    assert api().artifact_from_envelope(wire) == artifact
    wire['artifact']['summary'] = 'tampered'
    with pytest.raises(ValueError):
        api().artifact_from_envelope(wire)
    with pytest.raises(ValueError):
        api().Artifact(delivery, 'ack', 'valid', findings=['mutable'])
    with pytest.raises(ValueError):
        replace(delivery, run_id=True)


def test_external_named_real_profile_is_not_hijacked(conn, monkeypatch):
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, 'profile_exists', lambda name: name == 'external-existing')
    worker = Executor()
    seen = []
    lanes = api().ExternalLanes({'external-existing': api().Lane('fixture', worker)},
                                profile_spawn=lambda task, workspace, board=None: seen.append(task.id))
    tid = kb.create_task(conn, title='real profile', assignee='external-existing')
    dispatch.dispatch_once(conn, task_ids=[tid], spawn_fn=lanes)
    assert seen == [tid]
    assert worker.deliveries == []


def test_external_review_is_held_and_dry_run_has_no_delivery(conn, monkeypatch):
    monkeypatch.setattr(dispatch, 'review_dispatch_enabled', lambda: True)
    _tid, lanes, worker, _ = start(conn)
    review = kb.create_task(conn, title='review', assignee='external-fixture')
    conn.execute("UPDATE tasks SET status='review' WHERE id=?", (review,))
    conn.commit()
    result = dispatch.dispatch_once(conn, task_ids=[review], spawn_fn=lanes)
    assert result.skipped_nonspawnable == [review]
    assert len(worker.deliveries) == 1
    fresh = kb.create_task(conn, title='dry', assignee='external-fixture')
    result = dispatch.dispatch_once(conn, task_ids=[fresh], spawn_fn=lanes, dry_run=True)
    assert [row[0] for row in result.spawned] == [fresh]
    assert kb.get_task(conn, fresh).status == 'ready'
    assert not any(e.kind == 'external_intent' for e in kb.list_events(conn, fresh))


def test_submit_and_lookup_cannot_return_another_valid_tasks_result(conn):
    first, lanes, worker, first_delivery = start(conn)
    second = kb.create_task(conn, title='second synthetic', assignee='external-fixture')
    other = kb.claim_task(conn, second)
    class WrongReceiver:
        def submit(self, delivery):
            return api().Artifact(first_delivery, 'completed', 'wrong task result')
        def lookup(self, delivery):
            return self.submit(delivery)
    wrong = api().ExternalLanes({'external-fixture': api().Lane('fixture-local', WrongReceiver())})
    with pytest.raises(ValueError):
        wrong.deliver(conn, other, str(Path(first_delivery.workspace).parent / second))
    assert kb.get_task(conn, first).status == 'running'
    assert kb.get_task(conn, second).status == 'running'
    worker.receipt = api().Artifact(api().Delivery(**next(e.payload for e in kb.list_events(conn, second)
                                                        if e.kind == 'external_intent')), 'completed', 'second result')
    with pytest.raises(ValueError):
        lanes.reconcile(conn, first_delivery)
    assert kb.get_task(conn, second).status == 'running'


def test_changed_assignment_payload_cannot_accept_old_result(conn):
    tid, lanes, _, delivery = start(conn)
    conn.execute('UPDATE tasks SET body=? WHERE id=?', ('new assignment', tid))
    conn.commit()
    with pytest.raises(ValueError):
        lanes.receive(conn, api().Artifact(delivery, 'completed', 'old assignment result'))
    assert kb.get_task(conn, tid).status == 'running'


def test_reassignment_to_profile_cannot_replay_unresolved_external_effects(conn):
    tid, _lanes, worker, _delivery = start(conn, Executor('lost_ack'))
    assert kb.reclaim_task(conn, tid)
    conn.execute('UPDATE tasks SET assignee=? WHERE id=?', ('default', tid))
    conn.commit()
    seen = []
    result = dispatch.dispatch_once(conn, task_ids=[tid], spawn_fn=lambda *args: seen.append(args))
    assert result.spawned == []
    assert seen == []
    assert len(worker.deliveries) == 1


def test_crash_at_start_leaves_durable_intent_on_reopened_connection(conn):
    class Interrupted(Executor):
        def submit(self, delivery):
            self.deliveries.append(delivery)
            raise SystemExit('interrupted before executor start is known')
    worker = Interrupted()
    lanes = api().ExternalLanes({'external-fixture': api().Lane('fixture-local', worker)})
    tid = kb.create_task(conn, title='synthetic interrupted', assignee='external-fixture')
    task = kb.claim_task(conn, tid)
    with pytest.raises(SystemExit):
        lanes.deliver(conn, task, '/synthetic/workspace')
    with kbc.connect_closing() as reopened:
        lanes.deliver(reopened, kb.get_task(reopened, tid), '/synthetic/workspace')
        assert kb.get_task(reopened, tid).status == 'running'
    assert len(worker.deliveries) == 1
