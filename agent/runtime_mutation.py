"""Local, nonwaiting reservations for one agent's runtime mutations.

The mutex protects reservation bookkeeping only. Client construction, callbacks and
runtime installation execute outside it. Internal composition requires an explicit
one-use permit; ordinary same-thread reentry is refused just like a competing thread.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import Lock, get_ident
from uuid import uuid4


class RuntimeMutationBusyError(RuntimeError):
    """An agent already has a runtime mutation reserved."""


class RuntimeMutationStaleError(RuntimeMutationBusyError):
    """A deferred restore belongs to an earlier successful local selection."""


_OPERATIONS = frozenset({
    "switch_model", "restore_primary_runtime", "begin_voice_turn_route", "end_voice_turn_route", "one_turn_restore",
    "try_activate_fallback", "try_recover_primary_transport", "recover_with_credential_pool",
    "swap_credential", "revert_credential_rotation", "snapshot_primary_runtime",
    "refresh_codex_credentials", "refresh_nous_credentials", "refresh_env_credentials",
    "refresh_vertex_credentials", "refresh_provider_credentials", "refresh_anthropic_credentials",
})
_INITIALIZE_MUTEX = Lock()
_PERMIT: ContextVar[_CompositionPermit | None] = ContextVar("runtime_mutation_permit", default=None)


class RuntimeMutationState:
    """Per-agent bookkeeping; callers inspect or change shared fields under ``mutex``."""

    def __init__(self):
        self.mutex = Lock()
        self.lock = self.mutex
        self.revision = 0
        self.selection_token = uuid4().hex
        self.voice_lease = None
        self.cleanup_debt = None
        self.cleanup_requested = False
        self._voice_begin_ticket = None
        self._owner_token = None
        self._owner_thread = None

    @property
    def busy(self):
        with self.mutex:
            return self.busy_locked

    @property
    def busy_locked(self):
        """Read only while holding ``mutex`` when composing an atomic decision."""
        return self._owner_token is not None

    @property
    def successful_selection_token(self):
        """Read under ``mutex`` together with revision and voice recovery state."""
        return self.selection_token

    def voice_snapshot(self):
        with self.mutex:
            return (self.voice_lease, self.cleanup_debt, self.revision,
                    self.selection_token, self.busy_locked)

    @property
    def voice_setup_pending(self):
        with self.mutex:
            return self._voice_begin_ticket is not None

    def begin_voice_setup(self, active_lease):
        with self.mutex:
            if not isinstance(active_lease, RuntimeMutationLease) or active_lease.controller is not self:
                raise RuntimeMutationBusyError('voice setup requires the current lease')
            active_lease._check_owner_locked()
            if (self._voice_begin_ticket is not None or self.voice_lease is not None
                    or self.cleanup_debt is not None or self.cleanup_requested):
                raise RuntimeMutationBusyError('an earlier voice runtime still owns recovery')
            ticket = object()
            self._voice_begin_ticket = ticket
            return ticket

    def finish_voice_setup(self, active_lease, ticket):
        with self.mutex:
            active_lease._check_owner_locked()
            if self._voice_begin_ticket is not ticket:
                raise RuntimeError('voice setup ownership changed')
            self._voice_begin_ticket = None
            if self.voice_lease is None:
                self.cleanup_requested = False  # No voice route was bound; the cleanup is a no-op.

    def register_voice(self, active_lease, record):
        with self.mutex:
            if not isinstance(active_lease, RuntimeMutationLease) or active_lease.controller is not self:
                raise RuntimeMutationBusyError("voice registration requires the current lease")
            active_lease._check_owner_locked()
            if self.voice_lease is not None:
                if self.voice_lease is record:
                    return
                raise RuntimeMutationBusyError('voice recovery already has an owner')
            self.voice_lease = record
            self.cleanup_debt = record if self.cleanup_requested else None
            self.cleanup_requested = False

    def request_voice_cleanup(self, record=None, *, expected_revision=None, expected_selection=None):
        with self.mutex:
            if ((expected_revision is not None and expected_revision != self.revision)
                    or (expected_selection is not None and expected_selection != self.selection_token)):
                return False
            if self.voice_lease is not None and (record is None or self.voice_lease is record):
                self.cleanup_debt = self.voice_lease
                self.cleanup_requested = False
                return True
            if record is None and self.voice_lease is None:
                self.cleanup_requested = True
                return True
            return False

    def defer_voice(self, record):
        with self.mutex:
            if record is not None and self.voice_lease is record:
                self.cleanup_debt = record
                return True
            return False

    def finish_voice(self, record):
        with self.mutex:
            if record is None or self.voice_lease is not record:
                return False
            self.voice_lease = None
            if self.cleanup_debt is record:
                self.cleanup_debt = None
            return True

    def _record_successful_selection_locked(self):
        previous = self.voice_lease
        self.revision += 1
        self.selection_token = uuid4().hex
        self.voice_lease = self.cleanup_debt = None
        self.cleanup_requested = False
        return previous

    def record_successful_selection(self):
        """Commit selection bookkeeping from the currently reserved owner thread."""
        with self.mutex:
            if self._owner_token is None or self._owner_thread != get_ident():
                raise RuntimeMutationBusyError("selection requires the current reservation")
            return self._record_successful_selection_locked()


def get_runtime_mutation_state(agent) -> RuntimeMutationState:
    """Create exactly one controller, including for bare test/plugin agents."""
    with _INITIALIZE_MUTEX:
        state = getattr(agent, "_runtime_mutation_state", None)
        if state is None:
            state = RuntimeMutationState()
            agent._runtime_mutation_state = state
        elif not isinstance(state, RuntimeMutationState):
            raise TypeError("invalid runtime mutation state")
        return state


runtime_controller = get_runtime_mutation_state


def _check_operation(operation):
    if not isinstance(operation, str) or operation not in _OPERATIONS:
        raise ValueError("unknown local runtime mutation operation")


class _CompositionPermit:
    def __init__(self, lease, operation):
        self.agent = lease.agent
        self.controller = lease.controller
        self.token = lease.token
        self.thread = get_ident()
        self.operation = operation
        self.consumed = False


class RuntimeMutationLease:
    def __init__(self, agent, controller, token):
        self.agent = agent
        self.controller = controller
        self.token = token
        self._active = True

    def _check_owner_locked(self):
        if (not self._active or self.controller._owner_token is not self.token
                or self.controller._owner_thread != get_ident()):
            raise RuntimeMutationBusyError("runtime mutation lease is not current")

    def call(self, operation, function, *args, **kwargs):
        """Authorize exactly one nested entry while calling an internal implementation."""
        _check_operation(operation)
        with self.controller.mutex:
            self._check_owner_locked()
        permit = _CompositionPermit(self, operation)
        reset = _PERMIT.set(permit)
        try:
            return function(*args, **kwargs)
        finally:
            # Also revoke permits copied into another Context before consumption.
            permit.consumed = True
            _PERMIT.reset(reset)

    def successful_selection(self):
        """Invalidate previous voice recovery after a completed explicit model selection.

        Return the old voice record so projection cleanup can occur outside the mutex.
        Failed selection must never call this method.
        """
        with self.controller.mutex:
            self._check_owner_locked()
            return self.controller._record_successful_selection_locked()


def serialized_runtime_mutation(operation):
    """Guard a local method without holding the bookkeeping mutex during its body."""
    _check_operation(operation)

    def decorate(function):
        @wraps(function)
        def guarded(agent, *args, **kwargs):
            with runtime_mutation(agent, operation):
                return function(agent, *args, **kwargs)
        return guarded
    return decorate


def compose_runtime_call(agent, operation, function, *args, runtime_lease=None, **kwargs):
    """Compose only with an explicitly supplied current lease for this agent.

    Without a lease, use the ordinary entrypoint and its reservation guard. Being
    called on the owner's thread does not grant a callback an internal permit.
    """
    _check_operation(operation)
    if runtime_lease is None:
        return function(*args, **kwargs)
    if (not isinstance(runtime_lease, RuntimeMutationLease) or runtime_lease.agent is not agent
            or getattr(agent, '_runtime_mutation_state', None) is not runtime_lease.controller):
        raise RuntimeMutationBusyError('runtime composition requires this agent\'s current lease')
    return runtime_lease.call(operation, function, *args, **kwargs)


@contextmanager
def runtime_mutation(agent, operation):
    """Reserve this agent, or refuse immediately when another mutation owns it."""
    _check_operation(operation)
    state = get_runtime_mutation_state(agent)
    thread = get_ident()
    with state.mutex:
        owns_reservation = state._owner_token is None
        if owns_reservation:
            token = object()
            state._owner_token, state._owner_thread = token, thread
        else:
            permit = _PERMIT.get()
            if not (permit is not None and not permit.consumed
                    and permit.agent is agent and permit.controller is state
                    and permit.token is state._owner_token and permit.thread == thread
                    and state._owner_thread == thread and permit.operation == operation):
                raise RuntimeMutationBusyError("agent runtime mutation already reserved")
            permit.consumed = True  # consume BEFORE entering any nested body/callback
            token = state._owner_token
        lease = RuntimeMutationLease(agent, state, token)
    try:
        yield lease
    finally:
        with state.mutex:
            lease._active = False
            if owns_reservation:
                if state._owner_token is not token or state._owner_thread != thread:
                    raise RuntimeError("runtime mutation reservation ownership changed")
                state._owner_token = state._owner_thread = None
