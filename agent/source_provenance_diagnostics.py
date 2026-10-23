"""Bounded, owner-scoped fixed-code observations for the existing private receipt.

These observations never confer authority. Binding keys stay in memory, while
only enums and booleans can cross into the receipt's existing hash chain.
"""
from collections import OrderedDict, deque
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from threading import RLock
from functools import wraps

_STAGES = {
    'activation': frozenset({'active', 'missing_identity', 'no_registry', 'unexpected_error'}),
    'producer': frozenset({'no_context', 'ineligible_backend', 'result_ineligible',
        'source_unavailable', 'empty_slice', 'non_utf8', 'line_limit',
        'presentation_mismatch', 'grant_issued', 'unexpected_error',
        'symlink_path', 'invalid_content', 'invalid_line_range', 'slice_too_large',
        'missing_identity', 'canonical_path_unavailable', 'not_regular_file',
        'source_changed', 'read_policy_unavailable', 'sensitive_path',
        'content_mismatch', 'non_text_source', 'redaction_changed_content',
        'redaction_unavailable', 'grant_limit_exceeded', 'canonical_read_failed',
        'line_range_unavailable', 'symlink_check_failed', 'unsafe_hardlink'}),
    'commit': frozenset({'no_registry', 'no_request', 'no_grants',
        'binding_rejected', 'metadata_committed', 'unexpected_error'}),
    'consumer': frozenset({'bound', 'no_trace'}),
}
_BOOL_FIELDS = frozenset({'same_registry', 'request_matches', 'owner_matches', 'binding_present'})
_REFUSAL_ORIGINS = frozenset({
    'tool_result', 'source_bound', 'assistant_history', 'user_task',
    'system_or_schema', 'other',
})
_install_lock = RLock()
_active = ContextVar('source_diagnostic_producer', default=None)
_receipt = ContextVar('source_diagnostic_receipt', default=None)


class _ReceiptObservation(list):
    """Shared revocation survives copied ContextVars; close drops borrowed inputs."""
    def __init__(self, values):
        super().__init__(values)
        self.lock = RLock()
        self.closed = False

    def close(self):
        with self.lock:
            self.closed = True
            self.clear()


def _live_receipt(function):
    @wraps(function)
    def observe(*args, **kwargs):
        active = _receipt.get()
        if not isinstance(active, _ReceiptObservation):
            return None
        with active.lock:
            return None if active.closed else function(*args, **kwargs)
    return observe


def current_receipt_observation():
    return _receipt.get()


class _Trace:
    def __init__(self, registry):
        self.registry = registry
        self.events = deque(maxlen=32)
        self.overflow = False


class _State:
    def __init__(self):
        self.lock = RLock()
        self.traces = OrderedDict()


def _state(agent):
    with _install_lock:
        state = getattr(agent, '_source_provenance_diagnostic_state', None)
        if not isinstance(state, _State):
            state = _State()
            agent._source_provenance_diagnostic_state = state
        return state


def _event(stage, code, values):
    if type(stage) is not str or type(code) is not str or code not in _STAGES.get(stage, ()):
        return None
    return {'stage': stage, 'code': code, **{
        key: value for key, value in values.items()
        if key in _BOOL_FIELDS and type(value) is bool}}


def _observe(function):
    @wraps(function)
    def observed(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:  # health: allow BLE001 -- Private exception data must not escape optional diagnostics or affect admission.
            return None
    return observed


@_observe
def _observe_refusal_origins(callback):
    return callback()


@_observe
def record(agent, owner, registry, stage, code, **values):
    event = _event(stage, code, values)
    if event is None or not isinstance(owner, tuple) or len(owner) != 4:
        return
    state = _state(agent)
    with state.lock:
        trace = state.traces.get(owner)
        if trace is None:
            trace = state.traces[owner] = _Trace(registry)
            if len(state.traces) > 16:
                state.traces.popitem(last=False)
        trace.overflow |= len(trace.events) == trace.events.maxlen
        trace.events.append(event)


@_observe
def producer(code):
    active = _active.get()
    if active is not None:
        record(*active, 'producer', code)


@_observe
def producer_exception(exc):
    from agent.source_provenance import SourceProvenanceError
    code = exc.args[0] if isinstance(exc, SourceProvenanceError) and exc.args else None
    refusals = _STAGES['producer'] - {'grant_issued', 'unexpected_error'}
    producer(code if type(code) is str and code in refusals else 'unexpected_error')


@_observe
def _begin_activation(agent, owner, registry):
    return _active.set((agent, owner, registry))


@_observe
def _reset(variable, token):
    if token is not None:
        variable.reset(token)


@contextmanager
def activation(agent, owner, registry, context, code):
    token = _begin_activation(agent, owner, registry)
    try:
        with ExitStack() as stack:
            try:
                value = stack.enter_context(context)
            except Exception:
                record(agent, owner, registry, 'activation', 'unexpected_error')
                raise
            record(agent, owner, registry, 'activation', code)
            yield value
    finally:
        _reset(_active, token)


def _snapshot(agent, registry, owner):
    state = _state(agent)
    with state.lock:
        trace = state.traces.get(owner)
        if trace is None:
            return {'events': [{'stage': 'consumer', 'code': 'no_trace'}], 'overflow': False}
        events = list(trace.events)
        events.append({'stage': 'consumer', 'code': 'bound',
            'same_registry': trace.registry is registry, 'request_matches': True, 'owner_matches': True})
        return {'events': events[-32:], 'overflow': trace.overflow or len(events) > 32}


@_observe
def _begin_receipt(agent, registry, owner, refusal_origins=None):
    snapshot = _snapshot(agent, registry, owner)
    observation = _ReceiptObservation((owner, snapshot, refusal_origins, {'verified_source_scan': False}))
    return _receipt.set(observation), observation


@_observe
def _mask_receipt():
    return _receipt.set(None)


@contextmanager
def receipt_scope(agent, registry, owner, *, refusal_origins=None):
    begun = _begin_receipt(agent, registry, owner, refusal_origins)
    token, observation = begun if begun is not None else (_mask_receipt(), None)
    try:
        yield
    finally:
        if isinstance(observation, _ReceiptObservation):
            observation.close()
        _reset(_receipt, token)


@_observe
@_live_receipt
def record_native_metadata_scan(owner, segment, text, encoding_projection):
    """Borrow only the current renderer's validated native scan inputs, in memory."""
    active = _receipt.get()
    if active is None or active[0] != owner or len(active) < 4:
        return
    if type(text) is str and type(encoding_projection) is str:
        active[3].setdefault('native_metadata_scans', {})[id(segment)] = (
            segment, text, encoding_projection)


@_observe
@_live_receipt
def native_metadata_scan_inputs(segment):
    """Unobserved or invalid references supply no diagnostic content or authority."""
    active = _receipt.get()
    if active is None or len(active) < 4:
        return None
    observed = active[3].get('native_metadata_scans', {}).get(id(segment))
    return observed[1:] if observed is not None and observed[0] is segment else None


@_observe
@_live_receipt
def record_verified_source_scan(owner, secret_scan_inputs, encoding_scan_inputs,
                                exact_secret_values=()):
    """Remember only a fixed match from already-rendered, request-bound scan inputs."""
    active = _receipt.get()
    if (
        active is None or len(active) < 4 or active[0] != owner
        or not isinstance(owner, tuple) or len(owner) != 4
    ):
        return
    if not all(type(value) is str for value in (*secret_scan_inputs, *encoding_scan_inputs)):
        return
    if not all(type(value) is str for value in exact_secret_values):
        return
    from agent.llm_egress_firewall import (
        _contains_canonical_base64,
        _contains_exact_secret,
        _contains_private_absolute_path,
        _contains_secret,
    )

    if (
        _contains_secret(secret_scan_inputs)
        or _contains_exact_secret(secret_scan_inputs, exact_secret_values)
        or _contains_private_absolute_path(secret_scan_inputs)
        or _contains_canonical_base64(encoding_scan_inputs)
    ):
        active[3]['verified_source_scan'] = True


@_observe
@_live_receipt
def append_receipt_diagnostics(receipt, decision):
    """Only the matching decision can persist an independently filtered snapshot."""
    active = _receipt.get()
    owner = (decision.session_id, decision.turn_id, decision.request_id, decision.policy_digest)
    if active is None or active[0] != owner:
        return
    snapshot = active[1]
    events = []
    for item in snapshot['events'][-32:]:
        event = _event(item.get('stage'), item.get('code'), item)
        if event is not None:
            events.append(event)
    diagnostics = {'events': events, 'overflow': snapshot.get('overflow') is True}
    if decision.allowed is False and len(active) > 2 and callable(active[2]):
        evidence = _observe_refusal_origins(active[2])
        origins = set()
        has_typed_evidence = False
        if (
            isinstance(evidence, tuple) and len(evidence) == 2
            and type(evidence[1]) is bool
            and isinstance(evidence[0], (list, tuple, set, frozenset))
        ):
            candidates, has_typed_evidence = evidence
            origins = {
                item for item in candidates
                if type(item) is str and item in _REFUSAL_ORIGINS
            }
            if has_typed_evidence and not origins:
                origins.add('other')
        else:
            origins.add('other')
        if active[3].get('verified_source_scan') is True:
            origins.add('source_bound')
        if not has_typed_evidence and active[3].get('verified_source_scan') is not True:
            origins.add('other')
        diagnostics['refusal_origins'] = sorted(origins or {'other'})
    receipt['source_provenance_diagnostics'] = diagnostics
