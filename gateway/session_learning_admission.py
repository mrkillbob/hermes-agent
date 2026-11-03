"""Private captured-record admission; no delivery consumer or new ledger.

Only fixed text and source/digest metadata enter the existing admission store.
The bounded reference remains on its original volatile decision. Ordinary claim
refuses this marker before any restore, tools, prompt or model path. A durable
admission receipt is NOT session receipt/consumption or a durable model ACK.

Local order: SQLite writer -> native registry/transport lease -> selection lease.
Revocation never waits for the DB while holding these gates. Registry publication
and record revocation participate; unrelated memory/session/admin writers do not
gain an all-writer fence from this component. No RPC, grant, device mapping or
Codex conversation binding is created.
"""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict
from functools import partial
import hashlib
import threading
import time

from gateway.session_binding_generation import BindingScope, GenerationUnavailable, validate_observation
from gateway.session_contract import SessionRef
from gateway.session_learning_payload import canonical_json
from hermes_state_keys import admission_fingerprint
import hermes_state_runtime as runtime

MARKER = 'curated_reference_v1'
_TEXT = 'Curated reference prepared; delivery is unavailable.'


def _deny(reason='permission_denied'):
    raise runtime.RuntimeStoreError(reason)


def _scope(selected):
    f = selected.fence
    return BindingScope(f.principal_id, f.transport_id, f.profile_id, f.logical_session_id)


def _validate_owner(broker, selected):
    from gateway.session_authority import SessionAuthority
    if type(broker.authority) is not SessionAuthority or selected.observation is None:
        _deny('binding_generation_unavailable')
    try:
        validate_observation(selected.observation, broker.authority, _scope(selected))
    except GenerationUnavailable:
        _deny('binding_generation_unavailable')
    issuer = getattr(selected.connection, '_native_commit_issuer', None)
    if issuer is None or issuer.registry_binding is None:
        _deny('binding_generation_unavailable')
    observed = selected.observation.prepared
    binding = issuer.registry_binding
    if (binding[:4] != (observed.owner, observed.key, observed.slot, observed.revision)
            or binding[4] != observed.context):
        _deny('stale_generation')


class SelectionAdmission:
    """Original in-process permit; request fields cannot construct or redeem it."""
    def __init__(self, broker, selected, decision_id):
        self.broker, self.selected, self.decision_id = broker, selected, decision_id
        self.active, self.waiters, self.gate = False, [], threading.Lock()
        self.row = None
        f, observation = selected.fence, selected.observation
        exact_digest = hashlib.sha256(canonical_json(asdict(selected.exact)).encode()).hexdigest()
        self.payload = {'text': _TEXT, MARKER: {
            **asdict(f), **selected.reference.metadata(), 'decision_id': decision_id,
            'request_id': selected.request_id, 'selection_sha256': exact_digest,
            'registry_incarnation': observation.metadata_incarnation,
            'registry_revision': observation.metadata_revision}}
        if selected.execution is not None:
            self.payload[MARKER]['consumption_intent_sha256'] = selected.execution.intent_digest
        self.digest = admission_fingerprint(canonical_target=f.logical_session_id,
            payload={'input': self.payload, 'intent': 'queue'})
        self.native = selected.connection.issue_native_commit(operation_id='curated:' + decision_id,
            payload_sha256=self.digest, lifetime_s=120)

    def check_sql(self, conn):
        """Same transaction only; include the just-inserted row, never reopen DB."""
        f = self.selected.fence
        runtime._epoch(conn, f.authority_epoch)
        row = runtime._session(conn, f.logical_session_id)
        if (row['runtime_revision'] != f.revision or row['runtime_generation'] != f.selection_generation
                or runtime._canonical_chain(conn, f.logical_session_id)[-1] != f.physical_target):
            _deny('stale_generation')
        rows = conn.execute("SELECT * FROM session_admissions WHERE target_session_id=? "
                            "AND status IN ('queued','started','unknown')", (f.logical_session_id,)).fetchall()
        for pending in rows:
            if (pending['principal_id'] != f.principal_id or pending['request_id'] != self.selected.request_id
                    or pending['payload_digest'] != self.digest or pending['status'] != 'queued'):
                _deny('session_busy')

    @contextmanager
    def at_commit(self, conn):
        selected, broker = self.selected, self.broker
        # The existing SQLite writer keeps these reads stable until commit.
        # Do not acquire/read the database under either revocation gate.
        self.check_sql(conn)
        ref = SessionRef(selected.fence.profile_id, selected.fence.logical_session_id)
        if broker._native_live(selected.connection, ref) is not selected.live:
            _deny('stale_generation')
        with self.native.at_commit(conn):
            with broker._gate:
                if (selected.status != 'admitting' or time.monotonic() >= selected.expires_at
                        or selected.admission is not self):
                    _deny()
                if selected.execution is not None:
                    selected.execution.current(statuses=('admitting',))
                if (broker.authority.sessions.get(ref.session_id) is not selected.live
                        or not any(member is selected.connection.actor
                                   for member in selected.live.subscribers.values())):
                    _deny('stale_generation')
                if not self.gate.acquire(blocking=False):
                    _deny('reference_commit_busy')
                self.active = True
            try:
                yield
            finally:
                with broker._gate:
                    self.active = False
                    waiters, self.waiters = self.waiters, []
                    self.gate.release()
                for loop, future in waiters:
                    loop.call_soon_threadsafe(_finish_waiter, future)

    def committed(self, row):
        with self.broker._gate:
            if not self.native.guard_completed:
                _deny('reference_outcome_unknown')
            self.check_row(row)
            self.row = row
            self.selected.admission_id = row['admission_id']
            if self.selected.status == 'admitting':
                self.selected.status = 'admitted'
            if self.selected.execution is not None:
                authority = self.broker.authority
                if not hasattr(authority, '_reference_executions'):
                    authority._reference_executions = {}
                authority._reference_executions[row['admission_id']] = self.selected.execution
        return row

    def check_row(self, row):
        f = self.selected.fence
        if (row is None or row['principal_id'] != f.principal_id
                or row['target_session_id'] != f.logical_session_id
                or row['request_id'] != self.selected.request_id
                or admission_fingerprint(canonical_target=row['target_session_id'],
                    payload={'input': row['payload'], 'intent': row['intent']}) != self.digest
                or row['owner_epoch'] != f.authority_epoch
                or row['payload'] != self.payload
                or (self.selected.admission_id is not None
                    and row['admission_id'] != self.selected.admission_id)):
            _deny('admission_conflict')

    async def quiescent(self):
        with self.broker._gate:
            if not self.active:
                return
            loop = asyncio.get_running_loop()
            waiter = loop.create_future()
            self.waiters.append((loop, waiter))
        await asyncio.shield(waiter)


def _finish_waiter(future):
    if not future.done():
        future.set_result(None)


async def admit(broker, connection, decision_id, request_id):
    with broker._gate:
        selected = broker._selected(connection, decision_id, request_id)
        if selected.status != 'captured' or selected.reference is None:
            _deny()
        live = selected.live
    broker._unchanged(selected)
    _validate_owner(broker, selected)
    async with live.mutation_lock:
        broker._unchanged(selected)
        _validate_owner(broker, selected)
        with broker._gate:
            if (broker._selected(connection, decision_id, request_id) is not selected
                    or selected.status != 'captured' or selected.reference is None):
                _deny()
            permit = SelectionAdmission(broker, selected, decision_id)
            selected.admission, selected.status = permit, 'admitting'
        from gateway.session_runtime_workers import tracked_write
        f = selected.fence
        write = partial(runtime.admit_session_input, broker.authority.db,
            epoch=f.authority_epoch, principal_id=f.principal_id, session_id=f.logical_session_id,
            request_id=request_id, payload=permit.payload, _authorize_write=permit.check_sql,
            _replay_guard=permit.check_sql, _commit_guard=permit.at_commit)
        try:
            row = await tracked_write(broker.authority, write, then=permit.committed, ordered=True)
        except BaseException:
            with broker._gate:
                if selected.status == 'admitting':
                    selected.status = 'uncertain'
            raise
    return _receipt(broker, selected, permit, row)


def _receipt(broker, selected, permit, row):
    permit.check_row(row)
    current = False
    if selected.status == 'admitted' and time.monotonic() < selected.expires_at:
        try:
            _validate_owner(broker, selected)
            ref = SessionRef(selected.fence.profile_id, selected.fence.logical_session_id)
            handle = broker.authority._handle(ref)
            f = selected.fence
            current = (broker._native_live(selected.connection, ref, admission=False) is selected.live
                       and permit.native.receipt()['current_transport'] and row['status'] == 'queued'
                       and (handle.revision, handle.execution_generation) == (f.revision, f.selection_generation)
                       and broker.authority.physical_target(ref) == f.physical_target
                       and broker._source_sha() == f.source_sha)
        except (runtime.RuntimeStoreError, GenerationUnavailable):
            current = False
    return {**broker._metadata(permit.decision_id, selected),
            **permit.payload[MARKER],
            'schema': 'curated-admission-receipt-v1', 'admission_id': row['admission_id'],
            'admission_payload_sha256': permit.digest, 'admission_status': row['status'],
            'admission_outcome': row['outcome'], 'durable_admission': True,
            'current_selection': current,
            'authorization_scope': 'local-record-registry-transport-admission-only',
            'distributed_fence': False}


async def admission_receipt(broker, connection, decision_id, request_id):
    # Historical, body-free read does not redeem a revoked selection or reissue it.
    from gateway.session_learning import _identifier
    if not _identifier(decision_id) or not _identifier(request_id):
        _deny('invalid_params')
    with broker._gate:
        selected = broker._decisions.get(decision_id)
        if (selected is None or selected.connection is not connection or selected.request_id != request_id
                or selected.admission is None):
            _deny()
        permit = selected.admission
        if connection.actor is not permit.native.issuer.actor:
            _deny()
    row = await asyncio.to_thread(runtime.find_session_admission, broker.authority.db,
        principal_id=selected.fence.principal_id, session_id=selected.fence.logical_session_id,
        request_id=request_id)
    return _receipt(broker, selected, permit, row)


async def revoke_and_wait(broker, connection, decision_id, request_id):
    from gateway.session_learning import _identifier
    if not _identifier(decision_id) or not _identifier(request_id):
        _deny('invalid_params')
    with broker._gate:
        # Retire only this original handle, even after expiry, busy revocation,
        # close or cancelled cleanup. This never redeems or creates authority.
        selected = broker._decisions.get(decision_id)
        if (selected is None or selected.connection is not connection
                or selected.request_id != request_id):
            _deny()
        selected.status, selected.reference = 'revoked', None
        permit = selected.admission
    if permit is not None:
        await permit.quiescent()
    execution = selected.execution
    if execution is not None and execution.native_send is not None:
        await execution.native_send.issuer.quiescent()
    if execution is not None:
        from gateway.session_learning_cache import release_pin
        with broker._gate:
            execution.status, execution.history = 'revoked', []
        release_pin(execution)
    return {'decision_id': decision_id, 'status': 'revoked', 'session_acknowledged': False}
