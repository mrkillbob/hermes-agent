"""Concrete coordinator receipts never turn offers into model completion."""
from dataclasses import replace
import hashlib
from pathlib import Path
import threading
import time

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_external import ExternalLanes, Lane


def api():
    from hermes_cli import kanban_coordinator_executor as executor
    from hermes_cli import kanban_coordinator_runner as runner
    return executor, runner


@pytest.fixture
def context(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    kb.init_db()
    with kbc.connect_closing() as conn:
        yield conn, tmp_path


def offered(context, behavior='success', limits=None):
    conn, root = context
    ex, run = api()
    limits = limits or ex.CoordinatorLimits()
    database = Path(conn.execute('PRAGMA database_list').fetchone()['file']).resolve()
    executor = ex.CoordinatorExecutor(database, 'fixture-coordinator', limits)
    lanes = ExternalLanes({'external-fixture': Lane('fixture-coordinator', executor)})
    tid = kb.create_task(conn, title='fixture: coordinator receipt', body='inert fixture input', assignee='external-fixture')
    task = kb.claim_task(conn, tid)
    delivery = lanes.deliver(conn, task, str(root / tid))
    program = Path(run.__file__).parents[1] / 'tests/hermes_cli/fixtures/fake_coordinator_cli.py'
    plan = run.ExecutionPlan('fixture', hashlib.sha256(program.read_bytes()).hexdigest(), root / ('evidence-' + tid), behavior, hashlib.sha256(run.input_bytes(delivery)).hexdigest())
    return tid, delivery, executor, lanes, plan


def receive(context, delivery, plan, limits=None):
    ex, run = api()
    return run.receive_once(Path(delivery.database), delivery.task_id, delivery.run_id, delivery.key,
                            plan, limits or ex.CoordinatorLimits())


def kinds(conn, tid):
    return [event.kind for event in kb.list_events(conn, tid)]


@pytest.mark.platforms('posix')
def test_offer_ack_exit_result_are_distinct_and_lookup_never_reexecutes(context):
    conn, _ = context
    tid, delivery, executor, lanes, plan = offered(context, 'hold')
    assert executor.lookup(delivery).kind == 'accepted'
    assert kb.get_task(conn, tid).status == 'running'
    assert 'external_receiver_claim' not in kinds(conn, tid)
    outcome = []
    failure = []
    def receiver():
        try:
            outcome.append(receive(context, delivery, plan))
        except Exception as exc:
            failure.append(exc)
    thread = threading.Thread(target=receiver)
    thread.start()
    from tests.waiting import wait_until
    assert wait_until(lambda: (plan.evidence_root / 'ready').exists(), timeout=10)
    assert executor.lookup(delivery).kind == 'started'
    assert 'external_ack' in kinds(conn, tid)
    assert kb.get_task(conn, tid).status == 'running'
    (plan.evidence_root / 'release').write_bytes(b'go')
    thread.join(timeout=10)
    assert not thread.is_alive() and not failure
    assert outcome[0].state == 'completed'
    assert outcome[0].evidence_mode == 'inert_fixture'
    assert kb.get_task(conn, tid).status == 'done'
    before = [tuple(r) for r in conn.execute('SELECT * FROM task_events WHERE task_id=?', (tid,))]
    for _ in range(3):
        assert executor.lookup(delivery).kind == 'completed'
    assert [tuple(r) for r in conn.execute('SELECT * FROM task_events WHERE task_id=?', (tid,))] == before
    assert lanes.reconcile(conn, delivery)
    assert receive(context, delivery, plan).state == 'completed'
    assert kinds(conn, tid).count('external_launch_intent') == 1
    assert kinds(conn, tid).count('completed') == 1


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('behavior,want', [('success','completed'), ('question','blocked'), ('fail','error_settled'),
    ('wrong_binding','uncertain'), ('malformed','uncertain'), ('provider','uncertain'), ('fallback','uncertain')])
def test_exit_and_cli_receipts_are_validated(context, behavior, want):
    conn, _ = context
    tid, delivery, executor, _lanes, plan = offered(context, behavior)
    outcome = receive(context, delivery, plan)
    assert outcome.state == want
    assert kb.get_task(conn, tid).status == ({'completed':'done', 'blocked':'blocked', 'error_settled':'blocked'}.get(want, 'running'))
    exit_event = next(e for e in kb.list_events(conn, tid) if e.kind == 'external_process_exit')
    for stream in ('stdout', 'stderr'):
        data = (plan.evidence_root / stream).read_bytes()
        assert hashlib.sha256(data).hexdigest() == exit_event.payload[stream + '_sha256']
    before = kinds(conn, tid).count('external_launch_intent')
    executor.lookup(delivery)
    assert receive(context, delivery, plan).state == want
    assert kinds(conn, tid).count('external_launch_intent') == before == 1


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('behavior', ['ignore_term', 'overflow', 'descendant'])
def test_execution_deadline_output_cap_and_descendant_uncertainty(context, behavior):
    conn, _ = context
    ex, _run = api()
    limits = ex.CoordinatorLimits(execution_seconds=2, terminate_grace_seconds=.2, kill_grace_seconds=1)
    tid, delivery, executor, _lanes, plan = offered(context, behavior, limits)
    started = time.monotonic()
    outcome = receive(context, delivery, plan, limits)
    assert time.monotonic() - started < 8
    assert outcome.state in {'error_settled', 'uncertain'}
    assert kb.get_task(conn, tid).status != 'done'
    assert (plan.evidence_root / 'stdout').stat().st_size <= limits.stream_bytes
    if behavior == 'descendant':
        assert outcome.state == 'uncertain'
    assert executor.lookup(delivery).kind != 'completed'


def test_submit_lookup_are_bounded_and_lookup_is_read_only(context):
    conn, _ = context
    tid, delivery, executor, _lanes, _plan = offered(context)
    snapshot = [tuple(r) for r in conn.execute('SELECT * FROM task_events')]
    conn.execute('BEGIN IMMEDIATE')
    try:
        started = time.monotonic()
        assert executor.submit(delivery) is None
        assert time.monotonic() - started < 2
        started = time.monotonic()
        assert executor.lookup(delivery).kind == 'accepted'
        assert time.monotonic() - started < 1
    finally:
        conn.rollback()
    assert [tuple(r) for r in conn.execute('SELECT * FROM task_events')] == snapshot
    assert kb.get_task(conn, tid).status == 'running'


def test_plan_and_delivery_reject_live_tampered_or_foreign_inputs(context):
    conn, _ = context
    tid, delivery, executor, _lanes, plan = offered(context)
    for changed in (replace(delivery, key='foreign'), replace(delivery, executor='foreign')):
        with pytest.raises(ValueError):
            executor.lookup(changed)
    for changed in (replace(plan, mode='live'), replace(plan, program_sha256='0'*64), replace(plan, behavior='arbitrary')):
        with pytest.raises(ValueError):
            receive(context, delivery, changed)
    assert 'external_receiver_claim' not in kinds(conn, tid)
    assert kb.get_task(conn, tid).status == 'running'


@pytest.mark.parametrize('field,value', [('lookup_seconds', 0), ('submit_seconds', float('inf')),
    ('execution_seconds', 121), ('stream_bytes', 65537), ('kill_grace_seconds', True)])
def test_limits_cannot_disable_or_expand_bounds(field, value):
    with pytest.raises(ValueError):
        api()[0].CoordinatorLimits(**{field:value})


@pytest.mark.platforms('posix')
def test_recorded_exit_can_reconcile_without_launch_and_tampered_output_rejects(context, monkeypatch):
    conn, _ = context
    _ex, runner = api()
    tid, delivery, _executor, _lanes, plan = offered(context)
    finish = runner._finish
    def interrupted(*args):
        raise RuntimeError('crash after committed exit')
    monkeypatch.setattr(runner, '_finish', interrupted)
    with pytest.raises(RuntimeError):
        receive(context, delivery, plan)
    assert kb.get_task(conn, tid).status == 'running'
    assert 'external_process_exit' in kinds(conn, tid)
    monkeypatch.setattr(runner, '_finish', finish)
    assert receive(context, delivery, plan).state == 'completed'
    assert kinds(conn, tid).count('external_launch_intent') == 1

    tid2, delivery2, _executor2, _lanes2, plan2 = offered(context)
    monkeypatch.setattr(runner, '_finish', interrupted)
    with pytest.raises(RuntimeError):
        receive(context, delivery2, plan2)
    monkeypatch.setattr(runner, '_finish', finish)
    (plan2.evidence_root / 'stdout').write_bytes(b'changed')
    assert receive(context, delivery2, plan2).state == 'uncertain'
    assert kb.get_task(conn, tid2).status == 'running'


def _receiver_process(database, delivery, plan, pipe):
    pipe.send('ready')
    pipe.recv()
    try:
        outcome = api()[1].receive_once(database, delivery.task_id, delivery.run_id, delivery.key, plan, api()[0].CoordinatorLimits())
        pipe.send(outcome.state)
    finally:
        pipe.close()


@pytest.mark.platforms('posix')
def test_two_receivers_have_one_claim_and_one_launch(context):
    import multiprocessing
    import os
    conn, _ = context
    tid, delivery, _executor, _lanes, plan = offered(context, 'hold')
    spawn = multiprocessing.get_context('spawn')
    channels = [spawn.Pipe() for _ in range(2)]
    workers = [spawn.Process(target=_receiver_process, args=(Path(delivery.database), delivery, plan, child))
               for _parent, child in channels]
    try:
        for worker in workers:
            worker.start()
        for parent, child in channels:
            child.close()
            assert parent.poll(10) and parent.recv() == 'ready'
        for parent, _child in channels:
            parent.send('start')
        from tests.waiting import wait_until
        assert wait_until(lambda: (plan.evidence_root / 'ready').exists(), timeout=10)
        assert kb.get_task(conn, tid).status == 'running'
        from multiprocessing.connection import wait
        loser = wait([parent for parent, _child in channels], timeout=10)
        assert len(loser) == 1 and loser[0].recv() == 'uncertain'
        (plan.evidence_root / 'release').write_bytes(b'go')
        winner = next(parent for parent, _child in channels if parent is not loser[0])
        assert winner.poll(10) and winner.recv() == 'completed'
        assert kinds(conn, tid).count('external_receiver_claim') == 1
        assert kinds(conn, tid).count('external_launch_intent') == 1
    finally:
        try:
            fd = os.open(plan.evidence_root / 'release', os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
        except OSError:
            pass
        for worker in workers:
            worker.join(timeout=10)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=5)
        for parent, _child in channels:
            parent.close()


@pytest.mark.platforms('posix')
def test_crash_before_spawn_never_relaunches_and_reassignment_rejects(context, monkeypatch):
    conn, _ = context
    _ex, runner = api()
    tid, delivery, _executor, _lanes, plan = offered(context)
    original = runner.subprocess.Popen
    def interrupted(*args, **kwargs):
        raise SystemExit('crash in launch window')
    monkeypatch.setattr(runner.subprocess, 'Popen', interrupted)
    with pytest.raises(SystemExit):
        receive(context, delivery, plan)
    monkeypatch.setattr(runner.subprocess, 'Popen', original)
    assert receive(context, delivery, plan).state == 'uncertain'
    assert kinds(conn, tid).count('external_launch_intent') == 1
    assert 'external_process_started' not in kinds(conn, tid)

    tid2, delivery2, _executor2, _lanes2, plan2 = offered(context)
    assert kb.reclaim_task(conn, tid2)
    with pytest.raises(ValueError):
        receive(context, delivery2, plan2)
    assert 'external_receiver_claim' not in kinds(conn, tid2)


@pytest.mark.platforms('posix')
def test_lost_offer_reply_and_terminal_apply_crash_do_not_repeat_execution(context, monkeypatch):
    conn, root = context
    ex, runner = api()
    database = Path(conn.execute('PRAGMA database_list').fetchone()['file']).resolve()
    executor = ex.CoordinatorExecutor(database, 'fixture-coordinator', ex.CoordinatorLimits())
    original_submit = executor.submit
    def lost_reply(delivery):
        original_submit(delivery)
    monkeypatch.setattr(executor, 'submit', lost_reply)
    lanes = ExternalLanes({'external-fixture': Lane('fixture-coordinator', executor)})
    tid = kb.create_task(conn, title='fixture: lost reply', body='inert fixture input', assignee='external-fixture')
    delivery = lanes.deliver(conn, kb.claim_task(conn, tid), str(root / tid))
    assert executor.lookup(delivery).kind == 'accepted'
    plan = runner.ExecutionPlan('fixture', runner.FIXTURE_SHA256, root / ('evidence-' + tid), 'success',
                                hashlib.sha256(runner.input_bytes(delivery)).hexdigest())
    original_complete = kb.complete_task
    def interrupted(*args, **kwargs):
        raise RuntimeError('crash after terminal receipt commit')
    monkeypatch.setattr(kb, 'complete_task', interrupted)
    with pytest.raises(RuntimeError):
        receive(context, delivery, plan)
    assert kb.get_task(conn, tid).status == 'running'
    assert executor.lookup(delivery).kind == 'completed'
    monkeypatch.setattr(kb, 'complete_task', original_complete)
    assert receive(context, delivery, plan).state == 'completed'
    assert kinds(conn, tid).count('external_coordinator_offer') == 1
    assert kinds(conn, tid).count('external_launch_intent') == 1


@pytest.mark.platforms('posix')
def test_late_receiver_claim_remains_idempotent_after_winner_completed(context):
    conn, _root = context
    tid, delivery, executor, _lanes, plan = offered(context)
    assert receive(context, delivery, plan).state == 'completed'
    assert executor.record(delivery, 'external_receiver_claim', {'receiver_instance': 'late'}, exclusive=True) is False
    assert kinds(conn, tid).count('external_launch_intent') == 1
    assert kb.get_task(conn, tid).status == 'done'
