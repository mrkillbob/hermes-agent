"""Explicit HOST-only local consumption intent, original claim and settlement.

No RPC or production issuer. Capture/metadata consent does NOT arm consumption.
The extra host decision precedes admission and is bound into its existing row.
Only an already resident agent and a literal local HTTP destination qualify.
There is no platform conversation binding or semantic/human acknowledgment.
"""
import copy
from contextlib import contextmanager
from functools import partial
import hashlib
import time
from threading import Lock
from urllib.parse import urlsplit

import hermes_state_runtime as runtime
from gateway.session_contract import SessionRef
from gateway.session_learning_payload import canonical_json


def _deny(reason='reference_execution_unavailable'):
    raise runtime.RuntimeStoreError(reason)


def _resident(authority, ref, physical_target):
    from gateway.session_detached import running_key
    from run_agent import AIAgent
    lookup = getattr(authority.runner, '_resident_agent_for', None)
    agent = lookup(running_key(authority.runner, authority.sessions[ref.session_id].route,
                               ref.session_id)) if callable(lookup) else None
    if type(agent) is not AIAgent or agent.session_id != physical_target:
        _deny()
    return agent


def _runtime(agent):
    return (agent.provider, agent.model, agent.api_mode, agent.base_url)


def _destination(agent, destination, model):
    if type(destination) is not str or type(model) is not str:
        _deny()
    parsed = urlsplit(destination)
    if (type(destination) is not str or parsed.scheme != 'http'
            or parsed.hostname not in {'127.0.0.1', '::1'} or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != '/v1'
            or parsed.port is None or not 1 <= parsed.port <= 65535
            or destination != agent.base_url or model != agent.model
            or agent.provider != 'llamacpp' or agent.api_mode != 'chat_completions'):
        _deny()


class ReferenceExecution:
    """Volatile original host intent. Serializing equal fields cannot redeem it."""
    def __init__(self, broker, selected, decision_id, parent, history, destination, model):
        self.broker, self.selected, self.decision_id = broker, selected, decision_id
        self.authority, self.parent = broker.authority, parent
        self.ref = SessionRef(selected.fence.profile_id, selected.fence.logical_session_id)
        self.source, self.route = selected.live.source, selected.live.route
        self.memberships = tuple(key for key, member in selected.live.subscribers.items()
                                 if member is selected.connection.actor)
        self.route_epoch = self.authority.route_epoch(self.route)
        self.runtime, self.history = _runtime(parent), copy.deepcopy(history)
        self.status, self.generation, self.proof = 'armed', None, None
        self.native_send = self.native_claim = self.native_settle = None
        self.worker_gate, self.witness = Lock(), None
        self.intent = {'schema': 'curated-local-consumption-intent-v1',
            'destination': destination, 'model': model, 'attempt_limit': 1,
            'content_scope': 'normalized-summary-only', 'instruction_authority': 'none',
            'execution_permission': 'none', 'session_acknowledged': False}
        self.intent_digest = hashlib.sha256(canonical_json(self.intent).encode()).hexdigest()

    def current(self, *, statuses=('admitted',)):
        s, a = self.selected, self.authority
        if (self.status not in {'armed', 'claimed', 'executing', 'observed'}
                or s.status not in statuses or time.monotonic() >= s.expires_at
                or (s.reference is None and self.status != 'observed') or s.execution is not self
                or a.sessions.get(self.ref.session_id) is not s.live
                or s.live.source is not self.source or s.live.route != self.route
                or a.route_epoch(self.route) != self.route_epoch
                or any(s.live.subscribers.get(key) is not s.connection.actor for key in self.memberships)
                or _resident(a, self.ref, s.fence.physical_target) is not self.parent
                or _runtime(self.parent) != self.runtime
                or self.broker._native_live(s.connection, self.ref) is not s.live):
            _deny('stale_generation')

    @contextmanager
    def physical_scope(self):
        with self.broker._gate:
            if not self.worker_gate.acquire(blocking=False):
                _deny('reference_execution_busy')
            try:
                self.current()  # after gate entry, before any client/wire work
            except BaseException:
                self.worker_gate.release()
                raise
        try:
            yield
        finally:
            self.worker_gate.release()  # before native transport publishes quiescence

    def sql(self, conn, *, phase):
        s, f = self.selected, self.selected.fence
        runtime._epoch(conn, f.authority_epoch)
        session = runtime._session(conn, f.logical_session_id)
        row = runtime._row(runtime._admission(conn, s.admission_id))
        s.admission.check_row(row)
        expected = f.selection_generation + 1
        if (session['runtime_generation'] != expected
                or session['runtime_revision'] != f.revision + (phase == 'settle')
                or runtime._canonical_chain(conn, f.logical_session_id)[-1] != f.physical_target
                or row['generation'] != expected or row['status'] != ('terminal' if phase == 'settle' else 'started')):
            _deny('stale_generation')

    @contextmanager
    def commit(self, conn, *, phase):
        # SQLite -> native -> original record. No DB access under either memory gate.
        self.sql(conn, phase=phase)
        operation = self.native_claim if phase == 'claim' else self.native_settle
        with operation.at_commit(conn), self.broker._gate:
            self.current()
            yield

    def claimed(self, row):
        if not row or not self.native_claim.guard_completed:
            _deny('reference_outcome_unknown')
        self.selected.admission.check_row(row)
        self.generation, self.status = row['generation'], 'claimed'
        self.selected.delivery_generation = self.generation
        return self.authority._stamp_claim(self.ref, self.selected.live, row)


def prepare_execution(broker, connection, decision_id, request_id, *, conversation_history,
                      destination, model):
    """TRUSTED HOST ONLY, separate from capture consent; never request/note/model selected."""
    from gateway.session_learning_admission import _validate_owner
    with broker._gate:
        s = broker._selected(connection, decision_id, request_id)
        if s.status != 'captured' or s.execution is not None or type(conversation_history) is not list:
            _deny()
    broker._unchanged(s)
    _validate_owner(broker, s)
    ref = SessionRef(s.fence.profile_id, s.fence.logical_session_id)
    parent = _resident(broker.authority, ref, s.fence.physical_target)
    _destination(parent, destination, model)
    execution = ReferenceExecution(broker, s, decision_id, parent, conversation_history, destination, model)
    with broker._gate:
        if s.status != 'captured' or s.execution is not None:
            _deny()
        from gateway.session_learning_cache import pin_parent
        pin_parent(execution)
        s.execution = execution
        if not hasattr(broker.authority, '_reference_intents'):
            broker.authority._reference_intents = {}
        broker.authority._reference_intents[(id(broker), decision_id)] = execution
    return {'intent_sha256': execution.intent_digest, 'session_acknowledged': False}


def original_execution(authority, row):
    execution = getattr(authority, '_reference_executions', {}).get(row['admission_id'])
    if (type(execution) is not ReferenceExecution or execution.authority is not authority
            or execution.selected.execution is not execution):
        _deny()
    execution.selected.admission.check_row(row)
    return execution


async def claim_reference(authority, ref, live, first):
    execution = original_execution(authority, first)
    execution.current()
    if execution.status != 'armed' or execution.selected.live is not live:
        _deny()
    issuer = execution.selected.connection._native_commit_issuer
    execution.native_claim = issuer.issue(operation_id='curated-claim:' + execution.decision_id,
        payload_sha256=execution.selected.admission.digest)
    from gateway.session_runtime_workers import tracked_write
    return await tracked_write(authority, partial(runtime.claim_session_input, authority.db,
        epoch=authority.epoch, session_id=ref.session_id, head=first['admission_id'],
        _guard=authority._admission_gate(session_id=ref.session_id),
        _commit_guard=partial(execution.commit, phase='claim')), then=execution.claimed)


async def claim_or_refuse(authority, ref, live, first):
    try:
        return await claim_reference(authority, ref, live, first)
    except runtime.RuntimeStoreError:
        current = runtime.get_session_admission(authority.db, admission_id=first['admission_id'])
        if current is not None and current['status'] == 'started':
            from gateway.session_settlement_recovery import recover_failed_settlement
            await recover_failed_settlement(authority, current)
        else:
            await authority._refuse_head(ref, first['admission_id'], 'reference_delivery_unavailable')
        from gateway.session_learning_cache import release_pin
        for execution in getattr(authority, '_reference_intents', {}).values():
            if execution.selected.admission_id == first['admission_id']:
                execution.status, execution.history, execution.selected.reference = 'revoked', [], None
                release_pin(execution)
        return False


def settle_reference_sync(authority, row, result):
    execution = original_execution(authority, row)
    current = runtime.get_session_admission(authority.db, admission_id=row['admission_id'])
    if current['status'] == 'terminal':
        from gateway.session_results import admission_result
        from hermes_state_terminal import compact_result
        with authority.db._read_ctx() as conn:
            runtime._epoch(conn, authority.epoch)
        expected = compact_result(execution.proof, user_message=row['payload']['text'])
        if (current['owner_epoch'] != authority.epoch or current['generation'] != row['generation']
                or admission_result(authority.db, row['admission_id']) != expected
                or result != execution.proof):
            _deny('reference_outcome_unknown')
        execution.status = 'settled'
        from gateway.session_learning_cache import release_pin
        release_pin(execution)
        return current  # exact historical commit, never another delivery/permission
    execution.current()
    if (execution.status != 'observed' or execution.proof is None
            or result != execution.proof or execution.generation != row['generation']):
        _deny('reference_outcome_unknown')
    issuer = execution.selected.connection._native_commit_issuer
    execution.native_settle = issuer.issue(operation_id='curated-settle:' + execution.decision_id,
        payload_sha256=hashlib.sha256(canonical_json(result).encode()).hexdigest())
    saved = runtime.settle_session_input(authority.db, epoch=authority.epoch,
        admission_id=row['admission_id'], generation=row['generation'], outcome='completed',
        result=result, _commit_guard=partial(execution.commit, phase='settle'))
    execution.status = 'settled'
    execution.selected.reference, execution.history = None, []
    from gateway.session_learning_cache import release_pin
    release_pin(execution)
    return saved


async def settle_reference(authority, row):
    from gateway.session_runtime_workers import tracked_write
    return await tracked_write(authority, partial(settle_reference_sync, authority, row,
        authority.pending_results[row['admission_id']]))


def retire_unknown(authority, row):
    """Owner cleanup only; no read, permission renewal, result or acknowledgment."""
    from gateway.session_learning_cache import release_pin
    for execution in getattr(authority, '_reference_intents', {}).values():
        if execution.selected.admission_id == row['admission_id']:
            with execution.broker._gate:
                execution.status, execution.history, execution.selected.reference = 'unknown', [], None
            if execution.native_send is None or execution.native_send._physical_done.is_set():
                release_pin(execution)
