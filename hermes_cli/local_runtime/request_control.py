"""Attempt socket-scoped cancellation without inventing a native acknowledgement.

Idle child telemetry after closing a request is useful observation, not proof that
router forwarding has drained or a native task was cancelled. This source-only
helper reports IDLE_AFTER_CLOSE (cancelled=False). Supervisor readiness supplies
its verified incarnation and rechecks it before dispatch; generic callers supply
no owner authority. Exclusion covers cooperating helpers in this process only;
router/native cancellation proof remains unavailable.
Eviction is deliberately unavailable through this unbound helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import http.client
import json
import math
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from agent.memory_provider import spawn_context_thread
from hermes_cli.local_runtime.endpoint import child_query
from hermes_cli.local_runtime.request_io import DeadlineConnection, read_response

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_RESIDENT = ("loaded", "ready")
_LEASE_LOCK = threading.Lock()
_LEASES: set[tuple] = set()


def _seconds(value: float, *, zero: bool = True) -> float:
    if not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ValueError("timeout/interval must be finite and nonnegative")
    return value


class CancelOutcome(str, Enum):
    CONFIRMED_CANCELLED = (
        "confirmed_cancelled"  # reserved for a future native acknowledgement
    )
    IDLE_AFTER_CLOSE = (
        "idle_after_close"  # observation, never native cancellation proof
    )
    COMPLETED_BEFORE_CANCEL = "completed_before_cancel"
    NOT_CONFIRMED = "not_confirmed"
    UNVERIFIABLE = "unverifiable"
    MODEL_NOT_RESIDENT = "model_not_resident"
    EVICTED = "evicted"  # distinct from request cancellation; this helper cannot evict


class RequestRefused(RuntimeError):
    """No new request may be dispatched through this object."""


class ProbeError(RuntimeError):
    """Telemetry is unavailable, malformed or past its absolute deadline."""


class ModelNotResident(ProbeError):
    """A child telemetry route refused a nonresident model."""


@dataclass(frozen=True)
class CancelResult:
    outcome: CancelOutcome
    detail: str
    bound_slot: int | None = None
    bound_task: int | None = None
    idle_reads: int = 0
    elapsed_s: float = 0.0

    @property
    def cancelled(self) -> bool:
        return self.outcome is CancelOutcome.CONFIRMED_CANCELLED

    @property
    def completed(self) -> bool:
        return self.outcome is CancelOutcome.COMPLETED_BEFORE_CANCEL


@dataclass(frozen=True)
class ChildState:
    resident: bool
    busy_slots: tuple[tuple[int, int | None], ...]
    requests_processing: float
    requests_deferred: float

    @property
    def idle(self) -> bool:
        return (
            self.resident
            and not self.busy_slots
            and self.requests_processing == 0
            and self.requests_deferred == 0
        )


class ManagedChildProbe:
    """Loopback telemetry only; the name does not establish durable-owner authority."""

    def __init__(
        self, base: str, api_key: str, model_id: str, *, timeout_s: float = 5.0
    ):
        parts = urlsplit(base)
        if (
            parts.scheme != "http"
            or parts.hostname not in _LOOPBACK
            or not parts.port
            or parts.username
            or parts.password
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "endpoint must be a plain loopback HTTP origin with a port"
            )
        if (
            not isinstance(model_id, str)
            or not model_id
            or model_id != model_id.strip()
        ):
            raise ValueError("model must be an exact nonempty identifier")
        self.host = "127.0.0.1" if parts.hostname == "localhost" else parts.hostname
        self.port = parts.port
        self.api_key, self.model_id = api_key, model_id
        self.timeout_s = _seconds(timeout_s, zero=False)

    def _deadline(self, deadline: float | None) -> float:
        return (
            min(time.monotonic() + self.timeout_s, deadline)
            if deadline is not None
            else time.monotonic() + self.timeout_s
        )

    def _get(self, route: str, *, deadline: float | None = None) -> tuple[int, bytes]:
        conn = None
        try:
            conn = DeadlineConnection(self.host, self.port, self._deadline(deadline))
            conn.request(
                "GET", route, headers={"Authorization": f"Bearer {self.api_key}"}
            )
            return read_response(conn, limit=1 << 20)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise ProbeError(f"GET {route.split('?')[0]} failed") from exc
        finally:
            if conn is not None:
                conn.finish()

    def _json(self, route: str, *, deadline: float | None = None) -> object:
        status, body = self._get(route, deadline=deadline)
        if status == 400:
            raise ModelNotResident("model is not resident")
        if status != 200:
            raise ProbeError(f"telemetry HTTP {status}")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise ProbeError("non-JSON telemetry") from exc

    def model_status(self, *, deadline: float | None = None) -> str | None:
        data = self._json("/models", deadline=deadline)
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ProbeError("/models returned no data list")
        matches = [
            r for r in rows if isinstance(r, dict) and r.get("id") == self.model_id
        ]
        if len(matches) > 1:
            raise ProbeError("ambiguous model telemetry")
        if not matches:
            return None
        status = matches[0].get("status")
        if not isinstance(status, dict) or not isinstance(status.get("value"), str):
            raise ProbeError("model status is malformed")
        return status["value"]

    def read(self, *, deadline: float | None = None) -> ChildState:
        deadline = self._deadline(deadline)
        if time.monotonic() >= deadline:
            raise ProbeError("telemetry deadline expired")
        if self.model_status(deadline=deadline) not in _RESIDENT:
            return ChildState(False, (), 0.0, 0.0)
        query = child_query(self.model_id)
        try:
            slots = self._json(f"/slots?{query}", deadline=deadline)
            code, raw = self._get(f"/metrics?{query}", deadline=deadline)
        except ModelNotResident:
            return ChildState(False, (), 0.0, 0.0)
        if code == 400:
            return ChildState(False, (), 0.0, 0.0)
        if code != 200 or not isinstance(slots, list) or not slots:
            raise ProbeError("missing slot/metric telemetry")
        busy, ids = [], set()
        for slot in slots:
            if (
                not isinstance(slot, dict)
                or type(slot.get("is_processing")) is not bool
                or type(slot.get("id")) is not int
                or slot["id"] < 0
                or slot["id"] in ids
            ):
                raise ProbeError("invalid slot identity/processing state")
            ids.add(slot["id"])
            if slot["is_processing"]:
                task = slot.get("id_task")
                if type(task) is not int or task < 0:
                    raise ProbeError("busy slot lacks task identity")
                busy.append((slot["id"], task))
        values = {}
        for line in raw.decode("utf-8", "replace").splitlines():
            fields = line.split()
            if not fields or fields[0] not in (
                "llamacpp:requests_processing",
                "llamacpp:requests_deferred",
            ):
                continue
            try:
                value = float(fields[1]) if len(fields) == 2 else float("nan")
            except ValueError as exc:
                raise ProbeError("invalid request metric") from exc
            if (
                fields[0] in values
                or not math.isfinite(value)
                or value < 0
                or not value.is_integer()
            ):
                raise ProbeError("invalid/duplicate request metric")
            values[fields[0]] = value
        if len(values) != 2 or time.monotonic() >= deadline:
            raise ProbeError("missing metrics or expired telemetry")
        return ChildState(
            True,
            tuple(busy),
            values["llamacpp:requests_processing"],
            values["llamacpp:requests_deferred"],
        )


class ScopedRequest:
    """Single-use request; lifecycle facts and idle observations never confer owner authority."""

    def __init__(
        self, probe: ManagedChildProbe, body: dict, *, request_timeout_s: float = 600.0,
        lease_identity: tuple | None = None, owner_check: Callable[[], bool] | None = None,
    ):
        if body.get("model") != probe.model_id:
            raise ValueError("request model must match the telemetry model exactly")
        self._probe, self._payload = probe, json.dumps(body).encode()
        if (lease_identity is None) != (owner_check is None):
            raise ValueError('owner check and lease identity must be supplied together')
        if lease_identity is not None and (type(lease_identity) is not tuple or not callable(owner_check)):
            raise ValueError('owner lease requires an immutable identity and check')
        self._owner_check = owner_check
        self._request_timeout_s = _seconds(request_timeout_s, zero=False)
        self._lock, self._cancel_lock = threading.RLock(), threading.Lock()
        self._finished, self._cancel_intent = threading.Event(), threading.Event()
        self._conn = self._response = self._error = self._result = self._worker = None
        self._state = "new"
        self._lease = False
        self._lease_key = (
            getattr(probe, "host", id(probe)),
            getattr(probe, "port", None),
            probe.model_id,
            lease_identity,
        )
        self.bound_slot = self.bound_task = None
        self.binding = "unbound"

    def _check_owner(self) -> None:
        if self._owner_check is not None and not self._owner_check():
            raise RequestRefused('readiness owner incarnation changed')

    def _release_lease(self) -> None:
        with _LEASE_LOCK:
            if self._lease:
                _LEASES.discard(self._lease_key)
                self._lease = False

    def start(self, bind_timeout_s: float = 5.0, interval_s: float = 0.05) -> str:
        _seconds(bind_timeout_s, zero=False)
        _seconds(interval_s, zero=False)
        with self._lock:
            if self._state != "new" or self._cancel_intent.is_set():
                raise RequestRefused("request is single-use or already cancelled")
            self._state = "starting"
            with _LEASE_LOCK:
                if self._lease_key in _LEASES:
                    self._state = "refused"
                    raise RequestRefused(
                        "cooperative request lease is busy or quarantined"
                    )
                _LEASES.add(self._lease_key)
                self._lease = True
        self._request_deadline = time.monotonic() + self._request_timeout_s
        deadline = min(self._request_deadline, time.monotonic() + bind_timeout_s)
        try:
            self._check_owner()
            if not self._probe.read(deadline=deadline).idle:
                raise RequestRefused("child is not resident and idle")
            with self._lock:
                if self._cancel_intent.is_set():
                    raise RequestRefused("cancelled before dispatch")
                self._check_owner()
                self._state = "active"
                self._worker = spawn_context_thread(
                    self._run, name="scoped-llamacpp-request"
                )
                self._worker.start()
        except (ProbeError, RequestRefused):
            self._release_lease()
            with self._lock:
                self._state = "refused"
            raise RequestRefused("request refused before dispatch") from None
        while (
            time.monotonic() < deadline
            and not self._finished.is_set()
            and not self._cancel_intent.is_set()
        ):
            try:
                busy = self._probe.read(deadline=deadline).busy_slots
            except ProbeError:
                break
            if len(busy) == 1:
                self.bound_slot, self.bound_task = busy[0]
                self.binding = "observed"  # not proven to be this request's task
                return self.binding
            if len(busy) > 1:
                self.binding = "ambiguous"
                return self.binding
            self._finished.wait(min(interval_s, max(0.0, deadline - time.monotonic())))
        if self._response is not None:
            self.binding = "completed_before_bind"
        return self.binding

    def _run(self) -> None:
        conn = None
        send_attempted = False
        try:
            if self._cancel_intent.is_set():
                return
            conn = DeadlineConnection(
                self._probe.host, self._probe.port, self._request_deadline
            )
            with self._lock:
                self._conn = conn
                if self._cancel_intent.is_set():
                    return
            conn.connect()
            with self._lock:
                if self._cancel_intent.is_set():
                    return
                self._check_owner()
                # Once sending begins, a failure cannot establish that no bytes reached the router.
                send_attempted = True
                conn.request(
                    "POST",
                    "/v1/chat/completions",
                    body=self._payload,
                    headers={
                        "Authorization": f"Bearer {self._probe.api_key}",
                        "Content-Type": "application/json",
                    },
                )
            response = read_response(conn, limit=8 << 20)
            with self._lock:
                self._response = response
        except (OSError, ValueError, http.client.HTTPException, RequestRefused) as exc:
            self._error = exc
        finally:
            try:
                if conn is not None:
                    conn.finish()
            except (OSError, http.client.HTTPException) as exc:
                if self._error is None:
                    self._error = exc
            finally:
                if self._response is not None or not send_attempted:
                    self._release_lease()
                # Publish completion only after cleanup and lease disposition are settled.
                with self._lock:
                    self._state = "finished"
                    self._finished.set()
            # An interrupted POST stays quarantined: idle child telemetry cannot drain router work.

    def wait(self, timeout_s: float | None = None) -> tuple[int, bytes] | None:
        self._finished.wait(timeout_s)
        return self._response

    @property
    def error(self) -> BaseException | None:
        return self._error

    def _outcome(
        self, outcome: CancelOutcome, detail: str, started: float, reads: int = 0
    ) -> CancelResult:
        return CancelResult(
            outcome,
            detail,
            self.bound_slot,
            self.bound_task,
            reads,
            time.monotonic() - started,
        )

    def cancel(
        self,
        timeout_s: float = 30.0,
        *,
        settle_reads: int = 2,
        interval_s: float = 0.25,
    ) -> CancelResult:
        _seconds(timeout_s)
        _seconds(interval_s)
        if type(settle_reads) is not int or settle_reads < 1:
            raise ValueError("settle_reads must be a positive integer")
        started = time.monotonic()
        deadline = started + timeout_s
        self._cancel_intent.set()  # survives connection construction/publication races
        conn = self._conn
        if conn is not None:
            conn.abort()
        if not self._cancel_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return self._outcome(
                CancelOutcome.UNVERIFIABLE, "cancellation result still pending", started
            )
        try:
            if self._result is not None:
                return self._result
            # Do not wait for the lifecycle lock: a send may be blocked, but abort has already fired.
            if self._worker is None:
                self._result = self._outcome(
                    CancelOutcome.UNVERIFIABLE, "no dispatched request", started
                )
                return self._result
            self._finished.wait(min(0.05, max(0.0, deadline - time.monotonic())))
            self._result = self._verify(
                max(0.0, deadline - time.monotonic()), settle_reads, interval_s, started
            )
            return self._result
        finally:
            self._cancel_lock.release()

    def _verify(
        self, budget_s: float, settle_reads: int, interval_s: float, started: float
    ) -> CancelResult:
        deadline = time.monotonic() + max(0.0, budget_s)
        clean, problem, outcome = (
            0,
            "verification deadline expired",
            CancelOutcome.UNVERIFIABLE,
        )
        while time.monotonic() < deadline:
            if self._response is not None:
                return self._outcome(
                    CancelOutcome.COMPLETED_BEFORE_CANCEL,
                    "full response received",
                    started,
                )
            try:
                state = self._probe.read(deadline=deadline)
                if time.monotonic() >= deadline:
                    break
                if not state.resident:
                    clean, problem, outcome = (
                        0,
                        "child is not resident",
                        CancelOutcome.MODEL_NOT_RESIDENT,
                    )
                elif state.idle:
                    clean += 1
                    if clean >= settle_reads:
                        # Finished is published after the response and all transport cleanup.
                        # Never take a send-held lock here: cancellation has an absolute deadline.
                        if not self._finished.is_set():
                            return self._outcome(
                                CancelOutcome.UNVERIFIABLE,
                                "request transport still pending",
                                started,
                                clean,
                            )
                        if self._response is not None:
                            return self._outcome(
                                CancelOutcome.COMPLETED_BEFORE_CANCEL,
                                "full response received",
                                started,
                            )
                        return self._outcome(
                            CancelOutcome.IDLE_AFTER_CLOSE,
                            "transport ended; child observed idle; native/router cancellation unproven",
                            started,
                            clean,
                        )
                else:
                    clean, problem, outcome = (
                        0,
                        "still busy",
                        CancelOutcome.NOT_CONFIRMED,
                    )
            except ProbeError:
                if time.monotonic() >= deadline:
                    break
                clean, problem, outcome = (
                    0,
                    "telemetry unreadable",
                    CancelOutcome.UNVERIFIABLE,
                )
            if interval_s:
                time.sleep(min(interval_s, max(0.0, deadline - time.monotonic())))
        if self._response is not None:
            return self._outcome(
                CancelOutcome.COMPLETED_BEFORE_CANCEL, "full response received", started
            )
        return self._outcome(outcome, problem, started, clean)

    def evict(self, timeout_s: float = 60.0, interval_s: float = 0.25) -> CancelResult:
        """Unavailable: generic request objects have no durable-owner eviction capability."""
        _seconds(timeout_s)
        _seconds(interval_s)
        return self._outcome(
            CancelOutcome.UNVERIFIABLE,
            "eviction requires a separate durable-owner-authorized operation",
            time.monotonic(),
        )
