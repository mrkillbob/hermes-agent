"""Private request-local carrier lineage. Never add fields to provider payloads."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from agent.llm_egress_request_diagnostics import LineageTable, current_scope

_active = ContextVar("llm_egress_assembly_lineage", default=None)
_REASONS = frozenset({"base64_payload", "secret_detected", "exact_secret_detected", "private_absolute_path"})


@dataclass(frozen=True)
class LineageSnapshot:
    assembly: object
    table: object
    status: str
    candidates: tuple
    findings_overflow: bool
    lineage_overflow: bool


def snapshot_fields(observation):
    """Recheck revocation at receipt attachment under the admission's shared lock."""
    if (not isinstance(observation, LineageSnapshot) or _live() is not observation.assembly
            or observation.assembly.table is not observation.table):
        return None
    return {"lineage_status": observation.status,
            "lineage_overflow": observation.assembly.table.overflow or observation.lineage_overflow,
            "findings": [dict(candidate) for candidate in observation.candidates],
            "findings_overflow": observation.findings_overflow}


def capture_observer(agent, request, *, exact_secret_values=()):
    """Borrow scan inputs only until attempt cleanup; observe after the real renderer.

    The captured assembly/table identities prevent a retry or mutation barrier from
    supplying a different assembly to this exact authorization attempt.
    """
    assembly = _live()
    if assembly is None:
        return None
    table = assembly.table
    def observe():
        if _live() is not assembly or assembly.table is not table:
            return None
        return snapshot(agent, request, exact_secret_values=exact_secret_values)
    return observe


def record_constructed_turn(agent, row):
    """Called only where the turn constructor appends its own current user row."""
    try:
        scope = current_scope()
        if scope is not None:
            with scope._lock:
                if not scope._closed and scope._bound_agent is agent:
                    scope._constructed_turn = (agent, agent._current_turn_id, row)
    except Exception:  # health: allow BLE001 -- Optional observation cannot affect turn construction.
        return


class AssemblyLineage:
    def __init__(self, scope, agent, messages, anchor_index):
        self.scope = scope
        self.agent = agent
        self.session_id = agent.session_id
        self.turn_id = agent._current_turn_id
        self.table = LineageTable()
        self.closed = False
        anchor = getattr(scope, "_constructed_turn", None)
        self.valid_anchor = bool(
            anchor is not None and anchor[0] is agent and anchor[1] == self.turn_id
            and type(anchor_index) is int and 0 <= anchor_index < len(messages)
            and messages[anchor_index] is anchor[2]
            and isinstance(anchor[2], dict) and anchor[2].get("role") == "user"
        )
        if self.valid_anchor:
            self.table.seed((row, "prior_turn" if index < anchor_index else "current_turn")
                            for index, row in enumerate(messages))

    def close(self):
        self.closed = True
        self.table.close()
        self.agent = None


def begin_assembly(agent, messages, anchor_index):
    try:
        scope = current_scope()
        if scope is not None:
            with scope._lock:
                if (scope._closed or scope._bound_agent is not agent
                        or scope._bound_session_id != agent.session_id):
                    return None
                previous = getattr(scope, "_assembly", None)
                if previous is not None:
                    previous.close()
                assembly = AssemblyLineage(scope, agent, messages, anchor_index)
                scope._assembly = assembly
                return assembly
    except Exception:  # health: allow BLE001 -- Lineage setup cannot change assembly behavior.
        return None
    return None


@contextmanager
def assembly_context(assembly, *, close=False):
    token = _active.set(assembly)
    try:
        yield
    finally:
        _active.reset(token)
        if close and assembly is not None:
            with assembly.scope._lock:
                assembly.close()


def _live():
    assembly = _active.get()
    if (assembly is not None and not assembly.closed and not assembly.scope._closed
            and current_scope() is assembly.scope and assembly.scope._assembly is assembly):
        return assembly
    return None


def transfer(output, *inputs):
    """Observe a trusted copy or merge; return the identical output object."""
    try:
        assembly = _live()
        if assembly is not None:
            with assembly.scope._lock:
                if _live() is assembly and (assembly.table.get(output) != "unknown"
                        or any(assembly.table.get(row) != "unknown" for row in inputs)):
                    assembly.table.transfer(output, inputs)
    except Exception:  # health: allow BLE001 -- Copy observation cannot change payload bytes.
        return output
    return output


def generated(row):
    try:
        assembly = _live()
        if assembly is not None:
            with assembly.scope._lock:
                if _live() is assembly:
                    assembly.table.seed([(row, "request_generated")])
    except Exception:  # health: allow BLE001 -- Insertion observation cannot change the request.
        return row
    return row


def barrier():
    """Arbitrary mutation invalidates even reused objects, including their aliases."""
    try:
        assembly = _live()
        if assembly is not None:
            with assembly.scope._lock:
                if _live() is assembly:
                    assembly.table.close()
                    assembly.table = LineageTable()
                    assembly.valid_anchor = False
    except Exception:  # health: allow BLE001 -- Mutation observation cannot affect hooks.
        return


def close_turn(agent):
    try:
        scope = current_scope()
        if scope is not None:
            with scope._lock:
                if scope._bound_agent is agent:
                    assembly = getattr(scope, "_assembly", None)
                    if assembly is not None:
                        assembly.close()
                    scope._assembly = None
                    scope._constructed_turn = None
    except Exception:  # health: allow BLE001 -- Cleanup must not interfere with turn teardown.
        return


def snapshot(agent, request, *, exact_secret_values=()):
    """Candidate rescan of the exact projected typed rows, separate from final causes.

    Only fixed enums and bounded indexes leave this call. No matched values, paths,
    segments or secret tuples are retained. Native conversions remain unsupported.
    """
    assembly = _live()
    if (assembly is None or assembly.agent is not agent or assembly.session_id != request.session_id
            or assembly.turn_id != request.turn_id or agent.api_mode != "chat_completions"):
        return None
    with assembly.scope._lock:
        if _live() is not assembly:
            return None
        from agent.llm_egress_classifier import _typed_payload_violation_locations

        body = request.payload
        if not isinstance(body, dict):
            return None
        extra = body.get("extra_body")
        overridden = isinstance(extra, dict) and "messages" in extra
        collections = [(body.get("messages"), "shadowed" if overridden else "effective_message", False)]
        if overridden:
            collections.append((extra["messages"], "effective_message", True))
        candidates = []
        findings_overflow = False
        lineage_overflow = False
        partial = not assembly.valid_anchor
        for rows, carrier, untrusted in collections:
            if not isinstance(rows, (list, tuple)):
                partial = partial or carrier == "effective_message"
                continue
            for index, row in enumerate(rows[:2048]):
                if not isinstance(row, dict):
                    partial = True
                    continue
                temporal = "unknown" if untrusted else assembly.table.get(row)
                partial = partial or temporal == "unknown"
                reasons = {reason for _, _, _, found in _typed_payload_violation_locations(
                    row, exact_secret_values=exact_secret_values) for reason in found}
                for reason in sorted(reasons & _REASONS):
                    if len(candidates) == 16:
                        findings_overflow = True
                        continue
                    candidates.append({
                        "reason": reason, "basis": "candidate", "scan_stage": "other",
                        "carrier": carrier, "message_index": index,
                        "temporal": temporal,
                    })
            lineage_overflow = lineage_overflow or len(rows) > 2048
        status = "overflow" if assembly.table.overflow or lineage_overflow else "partial" if partial else "supported"
        return LineageSnapshot(assembly, assembly.table, status,
                               tuple(candidates), findings_overflow, lineage_overflow)
