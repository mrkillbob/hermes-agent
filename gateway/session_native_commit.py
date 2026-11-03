"""Same-process native TRANSPORT lifetime leases for the actual store commit.

No note/session permission, registry witness, device mapping, RPC, or distributed
hold_current is supplied. Only trusted host code calls issue; request/model/note
data cannot install an issuer. Native identity comes from the existing registered
AuthorityConnection, not a callback or a supplied authenticated=True field.
"""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import wraps
import os
import re
from threading import Lock
import time

from hermes_state_runtime import RuntimeStoreError


def _deny(reason='native_commit_unavailable'):
    raise RuntimeStoreError(reason)


@dataclass(frozen=True)
class NativeCommitScope:
    principal_id: str
    transport_id: str
    operation_id: str
    declared_payload_sha256: str


class NativeCommitOperation:
    """One original operation object, never reconstructed from serialized fields."""
    def __init__(self, issuer, scope, expires_at):
        self.issuer, self.scope, self.expires_at = issuer, scope, expires_at
        self.state = 'prepared'
        self.guard_completed = False

    def at_commit(self, sql_connection):
        return self.issuer.at_commit(self, sql_connection)

    def receipt(self):
        return self.issuer.receipt(self)


class NativeCommitIssuer:
    """One actual native connection; bounded volatile operations, no new ledger.

    The gate's domain is ONLY this registered transport. Close/rebind/unbind and
    sibling replacement join it. Session, registry, admin and external process
    writers are not covered, so this cannot authorize their operations by itself.
    """
    def __init__(self, connection):
        self.connection = connection
        self.actor, self.authority, self.transport = connection.actor, connection.authority, connection.transport
        self.profile_id, self.instance_id = self.authority.profile_id, self.authority.instance_id
        self.principal_id, self.transport_id = self.actor.subject, self.actor.transport_id
        self.process = os.getpid()
        self._gate, self._state = Lock(), Lock()
        self._closing, self._active = False, False
        self._waiters, self._operations = [], {}

    @property
    def closing(self):
        self._check_process()
        with self._state:
            return self._closing

    def _check_process(self):
        # A fork must fail before touching potentially inherited locked mutexes.
        if os.getpid() != self.process:
            _deny('native_commit_process_unavailable')

    def _current(self):
        from gateway.session_controls import AuthorityConnection
        c = self.connection
        if type(c) is not AuthorityConnection or os.getpid() != self.process:
            _deny()
        actor, authority, identity = c.actor, c.authority, c._identity
        if (actor is not self.actor or authority is not self.authority or c.transport is not self.transport
                or actor.subject != self.principal_id or actor.transport_id != self.transport_id
                or authority.profile_id != self.profile_id or authority.instance_id != self.instance_id
                or c.native_owner is not True or identity.get('provider') != 'local'
                or identity.get('profile_id') != actor.profile_id
                or actor.profile_id != authority.profile_id
                or identity.get('instance_id') != authority.instance_id
                or 'session:submit' not in actor.capabilities
                or getattr(authority, '_native_legacy_transports', {}).get(actor.transport_id) is not actor
                or authority.events.get(actor.transport_id) is not c.transport
                or getattr(authority, '_native_commit_issuers', {}).get(actor.transport_id) is not self):
            _deny()

    def issue(self, *, operation_id, payload_sha256, lifetime_s=60):
        """HOST ONLY. Native peer identity does not confer note/session permission."""
        self._check_process()
        if (type(operation_id) is not str or not 1 <= len(operation_id) <= 128
                or any(not 32 < ord(char) < 127 for char in operation_id)
                or type(payload_sha256) is not str or re.fullmatch('[0-9a-f]{64}', payload_sha256) is None
                or type(lifetime_s) is not int or not 1 <= lifetime_s <= 120):
            _deny('invalid_params')
        with self._state:
            if self._closing:
                _deny()
            self._current()
            existing = self._operations.get(operation_id)
            if existing is not None:
                if existing.scope.declared_payload_sha256 != payload_sha256:
                    _deny('admission_conflict')
                return existing  # never mint a replacement for committed/unknown work
            if len(self._operations) >= 64:
                _deny('native_commit_capacity')
            c = self.connection
            scope = NativeCommitScope(c.actor.subject, c.actor.transport_id, operation_id, payload_sha256)
            operation = NativeCommitOperation(self, scope, time.monotonic() + lifetime_s)
            self._operations[operation_id] = operation
            return operation

    @contextmanager
    def at_commit(self, operation, sql_connection):
        """Enter on the actual DB worker, AFTER SQL, around conn.commit only.

        Gate entry orders an already-entered commit before COMPLETED close.
        A close request blocks new entry immediately, then waits asynchronously.
        No DB/owner callbacks or waits are permitted while this gate is held.
        Active-to-inactive transaction state is checked, but cannot distinguish
        COMMIT from ROLLBACK. Receipts attest guard completion only; the trusted
        storage consumer supplies actual commit/result provenance separately.
        """
        self._check_process()
        with self._state:
            self._current()
            if (self._closing or type(operation) is not NativeCommitOperation
                    or operation.issuer is not self
                    or self._operations.get(operation.scope.operation_id) is not operation
                    or operation.state != 'prepared' or time.monotonic() >= operation.expires_at
                    or sql_connection is not self.authority.db._conn or not sql_connection.in_transaction):
                _deny()
            if not self._gate.acquire(blocking=False):
                _deny('native_commit_busy')
            self._active = True
            operation.state = 'committing'
        try:
            try:
                yield
            except BaseException:
                with self._state:
                    operation.state = 'unknown'
                raise
            else:
                with self._state:
                    if sql_connection.in_transaction:
                        operation.state = 'unknown'
                        _deny('native_commit_transaction_open')
                    operation.guard_completed = True
                    operation.state = 'guard-completed'
        finally:
            with self._state:
                self._active = False
                waiters, self._waiters = self._waiters, []
                self._gate.release()
            for loop, waiter in waiters:
                def finish(future=waiter):
                    if not future.done():
                        future.set_result(None)
                try:
                    loop.call_soon_threadsafe(finish)
                except RuntimeError:
                    if not loop.is_closed():
                        raise

    def begin_close(self):
        self._check_process()
        with self._state:
            self._closing = True

    async def quiescent(self):
        self._check_process()
        loop = asyncio.get_running_loop()
        with self._state:
            if not self._active:
                return
            waiter = loop.create_future()
            self._waiters.append((loop, waiter))
        await asyncio.shield(waiter)

    def receipt(self, operation):
        self._check_process()
        with self._state:
            if (type(operation) is not NativeCommitOperation
                    or self._operations.get(operation.scope.operation_id) is not operation):
                _deny()
            current = False
            if not self._closing and time.monotonic() < operation.expires_at and operation.guard_completed:
                try:
                    self._current()
                except RuntimeStoreError:
                    pass  # historical metadata remains readable through the original object
                else:
                    current = True
            return {'schema': 'native-transport-commit-v1', **asdict(operation.scope),
                    'state': operation.state, 'commit_guard_completed': operation.guard_completed,
                    'transaction_commit_attested': False,
                    'current_transport': current, 'authorization_scope': 'transport-lifetime-only',
                    'payload_content_attested': False, 'session_acknowledged': False,
                    'device_binding': 'unavailable', 'distributed_fence': False}

    @contextmanager
    def registration_change(self, actor):
        self._check_process()
        if actor is not self.actor:
            _deny('permission_denied')
        self.begin_close()
        if not self._gate.acquire(blocking=False):
            _deny('native_commit_busy')
        try:
            yield
        finally:
            self._gate.release()


@contextmanager
def native_registration_change(authority, actor):
    issuer = getattr(authority, '_native_commit_issuers', {}).get(actor.transport_id)
    if issuer is None:
        yield
    else:
        with issuer.registration_change(actor):
            yield
        if actor.transport_id not in getattr(authority, '_native_legacy_transports', {}):
            getattr(authority, '_native_commit_issuers', {}).pop(actor.transport_id, None)


def register_issuer(connection):
    """After constructor registration; no operation/consent is created."""
    if connection.native_owner is not True:
        return None
    issuer = NativeCommitIssuer(connection)
    authority = connection.authority
    if not hasattr(authority, '_native_commit_issuers'):
        authority._native_commit_issuers = {}
    authority._native_commit_issuers[connection.actor.transport_id] = issuer
    return issuer


def retire_native_sibling(connection):
    issuer = getattr(connection, '_native_commit_issuer', None)
    if issuer is not None:
        with issuer.registration_change(connection.actor):
            connection._reference_intake_open = False


def fenced_native_close(close):
    """Close NEW intake for the entire tree before the first asynchronous wait."""
    @wraps(close)
    async def guarded(connection):
        pending, issuers = [connection], []
        while pending:
            current = pending.pop()
            current._reference_intake_open = False
            issuer = getattr(current, '_native_commit_issuer', None)
            if issuer is not None:
                issuer.begin_close()
                issuers.append(issuer)
            pending.extend(current._siblings.values())
        for issuer in issuers:
            await issuer.quiescent()
        return await close(connection)
    return guarded
