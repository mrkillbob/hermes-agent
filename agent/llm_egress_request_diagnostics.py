"""Private, revocable request binding and bounded message-lineage observations.

This module carries no content and grants no authority. Its only durable output is
the allowlisted metadata returned by :func:`receipt_fields` for the matching
blocked request.
"""

from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass
import re
from threading import RLock
from typing import Any

_ADMISSION_ID = re.compile(r"[0-9a-f]{32}\Z")
_MAX_INT = 2_147_483_647
_MAX_ORDINAL = 65_535
_LINEAGES = frozenset({"current_turn", "prior_turn", "request_generated", "mixed", "unknown"})
_active: ContextVar = ContextVar("llm_egress_request_diagnostic", default=None)
_attempt_context: ContextVar = ContextVar("llm_egress_request_attempt", default=None)


@dataclass
class DiagnosticAttempt:
    ordinal: int | None
    agent: Any
    owner: tuple[str, str, str, str]
    request: Any
    lineage: Any = None
    recipient: Any = None
    receipt_context: Any = None

    def clear(self):
        if self.receipt_context is not None:
            self.receipt_context.close()
        self.receipt_context = None
        self.agent = self.request = self.owner = self.lineage = self.recipient = None


class DiagnosticScope:
    """One claimed admission's in-memory identity, attempts and revocable lifetime."""

    def __init__(self, *, admission_id=None, authority_epoch=None,
                 execution_generation=None, admission_sequence=None,
                 authority=None, profile_id=None, logical_session_id=None, source=None):
        self.admission_id = admission_id
        self.authority_epoch = authority_epoch
        self.execution_generation = execution_generation
        self.admission_sequence = admission_sequence
        self.authority = authority
        self.profile_id = profile_id
        self.logical_session_id = logical_session_id
        self.source = source
        self._lock = RLock()
        self._closed = False
        self._ordinal = 0
        self._attempts: dict[int, DiagnosticAttempt] = {}
        self.lineage = LineageTable()
        self._bound_agent = None
        self._bound_session_id = None
        self._ordinal_overflow = False
        self._claim_verified = False
        self._constructed_turn = None
        self._assembly = None

    def verify_claimed_turn(self, authority, owner, generation, source, durable_session_id):
        """Verify a direct owner/session/source match without another registry or DB lookup."""
        with self._lock:
            if (
                self._closed or self.authority is not authority
                or self.profile_id != getattr(authority, "profile_id", None)
                or owner != self.logical_session_id
                or generation != self.execution_generation
                or source != self.source
                or durable_session_id != owner
            ):
                return False
            self._claim_verified = True
            self._verified_session_id = durable_session_id
            return True

    def bind_turn_agent(self, agent, session_id, source):
        with self._lock:
            if self._closed or type(session_id) is not str:
                return False
            if not self._claim_verified or self._verified_session_id != session_id:
                return False
            if source != self.source:
                return False
            if self._bound_agent is not None:
                return self._bound_agent is agent and self._bound_session_id == session_id
            if self.authority is None or self.profile_id != getattr(self.authority, "profile_id", None):
                return False
            self._bound_agent = agent
            self._bound_session_id = session_id
            return True

    @property
    def closed(self):
        with self._lock:
            return self._closed

    def bind_request(self, request, *, agent, session_id, turn_id, request_id, policy_digest, lineage=None, recipient=None):
        owner = (session_id, turn_id, request_id, policy_digest)
        if not all(type(item) is str for item in owner):
            return None
        with self._lock:
            if self._closed:
                return None
            if self._bound_agent is None or self._bound_agent is not agent or self._bound_session_id != session_id:
                return None
            if self._ordinal_overflow or len(self._attempts) >= 8:
                self._ordinal_overflow = True
                return None
            self._ordinal += 1
            if self._ordinal > _MAX_ORDINAL:
                self._ordinal_overflow = True
                return None
            ordinal = self._ordinal
            attempt = DiagnosticAttempt(ordinal, agent, owner, request, lineage, recipient)
            self._attempts[id(attempt)] = attempt
            return attempt

    def end_attempt(self, attempt):
        with self._lock:
            if self._attempts.get(id(attempt)) is attempt:
                del self._attempts[id(attempt)]
                attempt.clear()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for attempt in self._attempts.values():
                attempt.clear()
            self._attempts.clear()
            self._bound_agent = None
            self._bound_session_id = None
            self.admission_id = None
            self.authority_epoch = None
            self.execution_generation = None
            self.admission_sequence = None
            self.authority = None
            self.profile_id = None
            self.logical_session_id = None
            self.source = None
            self._claim_verified = False
            self._verified_session_id = None
            self._constructed_turn = None
            if self._assembly is not None:
                self._assembly.close()
            self._assembly = None
            self.lineage.close()


class LineageTable:
    """Bounded strong-identity side table; values are fixed temporal enums only."""

    def __init__(self, *, max_associations=2048, max_transfers=8192):
        self._lock = RLock()
        self._max_associations = max_associations
        self._max_transfers = max_transfers
        self._rows: dict[int, tuple[Any, str]] = {}
        self._transfers = 0
        self._closed = False
        self.overflow = False

    def seed(self, pairs):
        """Seed validated ``(row, lineage)`` pairs, marking conflicting aliases unknown."""
        with self._lock:
            if self._closed:
                return
            for row, lineage in pairs:
                if not _is_row(row) or lineage not in _LINEAGES:
                    continue
                self._set(row, lineage)

    def transfer(self, output, inputs):
        """Record one trusted clone/merge edge; unknown input dominates conservatively."""
        with self._lock:
            if self._closed or not _is_row(output):
                return
            self._transfers += 1
            if self._transfers > self._max_transfers:
                self.overflow = True
                self._rows.pop(id(output), None)
                return
            values = [self._get_locked(item) for item in inputs if _is_row(item)]
            if not values or len(values) != len(inputs) or "unknown" in values:
                value = "unknown"
            else:
                distinct = set(values)
                value = next(iter(distinct)) if len(distinct) == 1 else "mixed"
            self._set(output, value)

    def get(self, row):
        with self._lock:
            if self._closed:
                return "unknown"
            return self._get_locked(row)

    def close(self):
        with self._lock:
            self._closed = True
            self._rows.clear()

    def _get_locked(self, row):
        entry = self._rows.get(id(row))
        return entry[1] if entry is not None and entry[0] is row else "unknown"

    def _set(self, row, lineage):
        key = id(row)
        existing = self._rows.get(key)
        if existing is not None and existing[0] is row:
            if existing[1] != lineage:
                self._rows[key] = (row, "unknown")
            return
        if len(self._rows) >= self._max_associations:
            self.overflow = True
            self._rows.pop(key, None)
            return
        self._rows[key] = (row, lineage)


def _is_row(value):
    return isinstance(value, dict)


def activate(scope):
    """Bind a scope for the current execution context and return its reset token."""
    return _active.set(scope)


def suspend():
    """Mask inherited admission identity while unsupported execution branches run."""
    return _active.set(None), _attempt_context.set(None)


def restore_suspended(token):
    try:
        active_token, attempt_token = token
        _active.reset(active_token)
        _attempt_context.reset(attempt_token)
    except Exception:  # health: allow BLE001 -- Restoring an optional observer must not alter work.
        return


def begin_admission(authority, ref, row, source):
    """Best-effort observation around an already-claimed ordinary admission."""
    active_token, attempt_token = suspend()
    try:
        scope = DiagnosticScope(
            admission_id=row.get("admission_id"),
            authority_epoch=row.get("owner_epoch"),
            execution_generation=row.get("generation"),
            admission_sequence=row.get("seq"),
            authority=authority,
            profile_id=ref.profile_id,
            logical_session_id=ref.session_id,
            source=source,
        )
        _active.set(scope)
        return scope, (active_token, attempt_token)
    except Exception:  # health: allow BLE001 -- Optional diagnostics must not prevent admitted work.
        return None, (active_token, attempt_token)


def end_admission(scope, token):
    """Revoke before resetting; neither cleanup error may mask turn behavior."""
    try:
        if scope is not None:
            scope.close()
    except Exception:  # health: allow BLE001, S110 -- Scope teardown is optional observation cleanup. Private exception text must not be logged.
        pass
    restore_suspended(token)


def reset(token):
    if token is not None:
        _active.reset(token)


def current_scope():
    scope = _active.get()
    return None if scope is None or scope.closed else scope


def bind_current_turn(agent, session_id, source):
    """Bind the durable agent session at the trusted gateway turn boundary."""
    try:
        scope = current_scope()
        return scope.bind_turn_agent(agent, session_id, source) if scope is not None else False
    except Exception:  # health: allow BLE001 -- Optional observation must not affect turn execution.
        return False


def verify_current_claimed_turn(
    authority, owner, generation, source, durable_session_id, *, expected_scope=None
):
    try:
        scope = current_scope()
        if expected_scope is not None and scope is not expected_scope:
            return False
        return (
            scope.verify_claimed_turn(authority, owner, generation, source, durable_session_id)
            if scope is not None else False
        )
    except Exception:  # health: allow BLE001 -- Optional association checks never affect turn routing.
        return False


def bind_actual_request(request, agent, *, session_id, turn_id, request_id, policy_digest, lineage=None, recipient=None):
    scope = current_scope()
    attempt = scope.bind_request(
        request, agent=agent, session_id=session_id, turn_id=turn_id,
        request_id=request_id, policy_digest=policy_digest, lineage=lineage, recipient=recipient,
    ) if scope is not None else None
    return scope, attempt


def activate_attempt(scope, attempt):
    return _attempt_context.set((scope, attempt))


def reset_attempt(token):
    if token is not None:
        _attempt_context.reset(token)


@contextmanager
def scoped_request(agent, request, context, *, exact_secret_values=(), route=None):
    """Bind the exact projected request only across its existing firewall call."""
    scope = None
    attempt = None
    try:
        scope = current_scope()
        if scope is not None:
            from agent.llm_egress_lineage import capture_observer
            scope, attempt = bind_actual_request(
                request, agent, session_id=request.session_id, turn_id=request.turn_id,
                request_id=request.request_id, policy_digest=request.policy_digest,
                lineage=capture_observer(agent, request, exact_secret_values=exact_secret_values),
                recipient=_recipient_tuple(route) if route is not None else None,
            )
    except Exception:  # health: allow BLE001 -- Binding diagnostics are optional.
        scope, attempt = None, None
    token = _attempt_context.set((scope, attempt) if scope is not None and attempt is not None else None)
    try:
        with context:
            _attach_receipt_context(scope, attempt)
            yield
    finally:
        try:
            if scope is not None and attempt is not None:
                scope.end_attempt(attempt)
        except Exception:  # health: allow BLE001, S110 -- Optional attempt teardown cannot affect authorization. Private exception text must not be logged.
            pass
        try:
            reset_attempt(token)
        except Exception:  # health: allow BLE001, S110 -- Optional context teardown cannot affect authorization. Private exception text must not be logged.
            pass


def append_receipt_binding(
    receipt, decision, *, request, final_scan_values=(), final_scan_origins=()
):
    active = _attempt_context.get()
    if active is None or getattr(decision, "allowed", True):
        return

    try:
        scope, attempt = active
        with scope._lock:
            if attempt.recipient is not None and attempt.recipient != _recipient_tuple(decision):
                return
            fields = receipt_fields(
                scope, attempt, request=request,
                owner=(decision.session_id, decision.turn_id, decision.request_id, decision.policy_digest),
            )
            if fields is not None and fields["request_binding"] == "exact":
                final, overflow = _final_scan_findings(
                    getattr(decision, "reason_codes", ())
                )
                combined = final + fields["findings"]
                fields["findings"] = combined[:16]
                fields["findings_overflow"] = fields["findings_overflow"] or overflow or len(combined) > 16
                try:
                    attribution = _final_scan_attribution(
                        getattr(decision, "reason_codes", ()),
                        final_scan_values,
                        final_scan_origins,
                    )
                except Exception:  # health: allow BLE001 -- Optional correlation cannot drop the refusal receipt.
                    attribution = [{"reason": "base64_payload", "carrier": "aggregate"}]
                if attribution:
                    fields["final_scan_attribution"] = attribution
                receipt["refusal_request_v1"] = fields
    except Exception:  # health: allow BLE001 -- Optional diagnostics cannot change egress outcomes.
        return


def _attach_receipt_context(scope, attempt):
    try:
        if scope is not None and attempt is not None:
            from agent.source_provenance_diagnostics import current_receipt_observation
            observation = current_receipt_observation()
            if observation is not None:
                with scope._lock:
                    if scope._closed or scope._attempts.get(id(attempt)) is not attempt:
                        observation.close()
                    else:
                        attempt.receipt_context = observation
    except Exception:  # health: allow BLE001 -- Optional cleanup association cannot affect authorization.
        return


def _final_scan_findings(reason_codes):
    """Name only fixed reasons actually emitted by the final firewall predicates."""
    reason_map = {
        "base64_payload": "base64_payload",
        "secret_detected": "secret_detected",
        "exact_secret_detected": "exact_secret_detected",
        "private_absolute_path": "private_absolute_path",
    }
    findings = [
        {
            "reason": code,
            "scan_stage": "aggregate",
            "basis": "final_scan",
            "carrier": "aggregate",
            "temporal": "unknown",
        }
        for code in dict.fromkeys(reason_map.get(code, "other") for code in reason_codes)
    ]
    return findings[:16], len(findings) > 16


def _final_scan_attribution(reason_codes, scan_values, scan_origins):
    """Correlate scanner hits using content-free origins captured during rendering."""
    if "base64_payload" not in reason_codes or not scan_values:
        return []
    from agent.llm_egress_firewall import _contains_canonical_base64

    findings = set()
    try:
        for index, value in enumerate(scan_values):
            if not isinstance(value, str) or not _contains_canonical_base64(value):
                continue
            origin = scan_origins[index] if index < len(scan_origins) else None
            if (
                isinstance(origin, tuple) and len(origin) == 2
                and origin[0] in {"tool_result", "user_message", "message_content"}
                and type(origin[1]) is int and origin[1] >= 0
            ):
                findings.add(("base64_payload", origin[0], origin[1]))
            else:
                findings.add(("base64_payload", "aggregate", None))
    except Exception:  # health: allow BLE001 -- Optional attribution must not affect egress.
        return [{"reason": "base64_payload", "carrier": "aggregate"}]

    if not findings:
        return [{"reason": "base64_payload", "carrier": "aggregate"}]
    ordered = [
        ({"reason": reason, "carrier": carrier, **({"message_index": index} if index is not None else {})})
        for reason, carrier, index in sorted(
            findings, key=lambda item: (item[1], -1 if item[2] is None else item[2])
        )
    ]
    if len(ordered) > 16:
        return [*ordered[:15], {"reason": "base64_payload", "carrier": "aggregate"}]
    return ordered


def receipt_fields(scope, attempt, *, request, owner=None):
    """Snapshot fixed receipt fields only for the exact live request object."""
    if not isinstance(scope, DiagnosticScope) or not isinstance(attempt, DiagnosticAttempt):
        return None
    with scope._lock:
        if scope._closed:
            return None
        if scope._attempts.get(id(attempt)) is not attempt:
            return None
        exact = attempt.request is request and (owner is None or owner == attempt.owner)
        admission_id = scope.admission_id
        fields = {
            "version": 1,
            "authorization_ordinal": attempt.ordinal,
            "admission_binding": "bound" if _ADMISSION_ID.fullmatch(admission_id or "") else "unavailable",
            "request_binding": "exact" if exact else "mismatch",
            "lineage_status": "overflow" if scope.lineage.overflow else "unavailable",
            "findings": [],
            "findings_overflow": False,
            "lineage_overflow": scope.lineage.overflow,
        }
        if _ADMISSION_ID.fullmatch(admission_id or ""):
            fields["admission_ref"] = admission_id
        for key, value in (
            ("authority_epoch", scope.authority_epoch),
            ("execution_generation", scope.execution_generation),
            ("admission_sequence", scope.admission_sequence),
        ):
            if type(value) is int and 0 <= value <= _MAX_INT:
                fields[key] = value
        if scope._ordinal_overflow:
            fields["admission_binding"] = "overflow"
        if attempt.lineage is not None:
            from agent.llm_egress_lineage import snapshot_fields
            lineage_fields = snapshot_fields(attempt.lineage())
            if lineage_fields is not None:
                fields.update(lineage_fields)
        return fields


def _recipient_tuple(route):
    from collections.abc import Mapping
    def value(name):
        return route.get(name, "") if isinstance(route, Mapping) else getattr(route, name, "")
    return (str(value("provider")), str(value("model")),
            str(value("base_url") or ""), str(value("api_mode") or ""))


def private_receipt_scope(agent, registry, owner, request, route, exact_secret_values):
    from agent.llm_egress_refusal_origins import refusal_receipt_scope
    return scoped_request(agent, request, refusal_receipt_scope(
        agent, registry, owner, request.payload, exact_secret_values=exact_secret_values),
        exact_secret_values=exact_secret_values, route=route)


def append_private_receipt_diagnostics(
    receipt, decision, *, request, final_scan_values=(), final_scan_origins=()
):
    from agent.source_provenance_diagnostics import append_receipt_diagnostics
    append_receipt_diagnostics(receipt, decision)
    append_receipt_binding(
        receipt, decision, request=request,
        final_scan_values=final_scan_values, final_scan_origins=final_scan_origins,
    )


__all__ = ["DiagnosticAttempt", "DiagnosticScope", "LineageTable", "activate",
           "activate_attempt", "append_receipt_binding", "begin_admission", "bind_actual_request",
           "bind_current_turn", "current_scope", "end_admission", "receipt_fields", "reset",
           "reset_attempt", "restore_suspended", "scoped_request", "suspend", "verify_current_claimed_turn"]
