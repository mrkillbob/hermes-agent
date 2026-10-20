"""Request-scoped cancellation of one managed llama.cpp chat request, with verified completion.

The router ends a generation when the client connection that carries it closes; there is no
request-ID cancel endpoint. So the only cancel primitive here is closing OUR socket, and the only
evidence that the work stopped is the server's own telemetry (``/slots``, ``/metrics``, ``/models``)
read afterwards. A client exit, a timeout, or a closed socket is never reported as a cancellation:
``CancelResult.cancelled`` is True only after the server shows no processing slot, no processing or
deferred request, our bound task gone, and the model child still resident, on consecutive reads.

Scope: one request at a time against an otherwise idle child (``start`` refuses a busy child), so
"nothing is processing" is a statement about this request. If a foreign request appears while ours
runs, binding is ``ambiguous`` and verification will not claim a cancel while anything is busy.
The residual race (a task queued after the last idle read) is bounded by ``settle_reads`` x
``interval_s`` and documented on ``cancel``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import http.client
import json
import socket
import threading
import time
from urllib.parse import urlsplit

from agent.memory_provider import spawn_context_thread
from hermes_cli.local_runtime.endpoint import child_query

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_RESIDENT = ("loaded", "ready")


class CancelOutcome(str, Enum):
    CONFIRMED_CANCELLED = "confirmed_cancelled"      # closed early; server idle, task gone
    COMPLETED_BEFORE_CANCEL = "completed_before_cancel"  # full response arrived; nothing to cancel
    NOT_CONFIRMED = "not_confirmed"                  # server still busy at the deadline
    UNVERIFIABLE = "unverifiable"                    # telemetry unreadable; nothing is claimed
    MODEL_NOT_RESIDENT = "model_not_resident"        # child gone: idle is vacuous, not an ack
    EVICTED = "evicted"                              # explicit evict(): child unloaded and confirmed


class RequestRefused(RuntimeError):
    """The request was not sent: the child is busy, not resident, or telemetry is unreadable."""


class ProbeError(RuntimeError):
    """A telemetry read failed or returned something unparseable."""


class ModelNotResident(ProbeError):
    """The router answered 400 for a status read: the child is not loaded."""


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
        return self.outcome in (CancelOutcome.CONFIRMED_CANCELLED, CancelOutcome.EVICTED)

    @property
    def completed(self) -> bool:
        return self.outcome is CancelOutcome.COMPLETED_BEFORE_CANCEL


@dataclass(frozen=True)
class ChildState:
    resident: bool
    busy_slots: tuple[tuple[int, int | None], ...]  # (slot id, id_task) of processing slots
    requests_processing: float
    requests_deferred: float

    @property
    def idle(self) -> bool:
        return (self.resident and not self.busy_slots
                and self.requests_processing == 0 and self.requests_deferred == 0)


class ManagedChildProbe:
    """Read-only telemetry for one router child. Fails closed: any gap raises ``ProbeError``."""

    def __init__(self, base: str, api_key: str, model_id: str, *, timeout_s: float = 5.0):
        parts = urlsplit(base)
        if parts.scheme != "http" or (parts.hostname or "") not in _LOOPBACK or not parts.port:
            raise ValueError(f"managed endpoint must be http loopback with a port: {base!r}")
        self.host, self.port = parts.hostname, parts.port
        self.api_key, self.model_id, self.timeout_s = api_key, model_id, timeout_s

    def _get(self, route: str) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_s)
        try:
            conn.request("GET", route, headers={"Authorization": f"Bearer {self.api_key}"})
            resp = conn.getresponse()
            return resp.status, resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise ProbeError(f"GET {route.split('?')[0]} failed: {exc}") from exc
        finally:
            conn.close()

    def _json(self, route: str) -> object:
        status, body = self._get(route)
        if status == 400:
            raise ModelNotResident(f"{route.split('?')[0]} -> 400")
        if status != 200:
            raise ProbeError(f"{route.split('?')[0]} -> HTTP {status}")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise ProbeError(f"{route.split('?')[0]} returned non-JSON") from exc

    def model_status(self) -> str | None:
        data = self._json("/models")
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ProbeError("/models returned no data list")
        for row in rows:
            if isinstance(row, dict) and row.get("id") == self.model_id:
                status = row.get("status")
                return status.get("value") if isinstance(status, dict) else None
        return None

    def read(self) -> ChildState:
        status = self.model_status()
        if status not in _RESIDENT:
            return ChildState(False, (), 0.0, 0.0)
        query = child_query(self.model_id)
        try:
            slots = self._json(f"/slots?{query}")
            code, raw = self._get(f"/metrics?{query}")
        except ModelNotResident:
            return ChildState(False, (), 0.0, 0.0)
        if not isinstance(slots, list):
            raise ProbeError("/slots returned no list")
        if code == 400:
            return ChildState(False, (), 0.0, 0.0)
        if code != 200:
            raise ProbeError(f"/metrics -> HTTP {code}")
        busy = []
        for slot in slots:
            if not isinstance(slot, dict) or "is_processing" not in slot or "id" not in slot:
                raise ProbeError("/slots row lacks id/is_processing")
            if slot["is_processing"]:
                task = slot.get("id_task")
                busy.append((int(slot["id"]), int(task) if isinstance(task, int) else None))
        values = {}
        for line in raw.decode("utf-8", "replace").splitlines():
            for key in ("requests_processing", "requests_deferred"):
                if line.startswith(f"llamacpp:{key}"):
                    try:
                        values[key] = float(line.split()[-1])
                    except ValueError as exc:
                        raise ProbeError(f"unparseable metric {key}") from exc
        if len(values) != 2:
            # The existing idle sweeper treats a missing line as idle; a cancel proof must not.
            raise ProbeError("/metrics lacks requests_processing/requests_deferred")
        return ChildState(True, tuple(busy), values["requests_processing"],
                          values["requests_deferred"])

    def unload(self) -> None:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=120)
        try:
            conn.request("POST", "/models/unload", body=json.dumps({"model": self.model_id}),
                         headers={"Authorization": f"Bearer {self.api_key}",
                                  "Content-Type": "application/json"})
            resp = conn.getresponse()
            resp.read()
            if resp.status != 200:
                raise ProbeError(f"/models/unload -> HTTP {resp.status}")
        except (OSError, http.client.HTTPException) as exc:
            raise ProbeError(f"/models/unload failed: {exc}") from exc
        finally:
            conn.close()


class ScopedRequest:
    """One chat-completions request that this object alone may cancel."""

    def __init__(self, probe: ManagedChildProbe, body: dict, *, request_timeout_s: float = 600.0):
        self._probe = probe
        self._payload = json.dumps(body).encode()
        self._request_timeout_s = request_timeout_s
        self._lock = threading.Lock()
        self._finished = threading.Event()
        self._conn: http.client.HTTPConnection | None = None
        self._response: tuple[int, bytes] | None = None  # set only by a fully read response
        self._error: BaseException | None = None
        self._result: CancelResult | None = None
        self._worker: threading.Thread | None = None
        self.bound_slot: int | None = None
        self.bound_task: int | None = None
        self.binding = "unbound"  # bound | ambiguous | completed_before_bind | unbound

    # ── lifecycle ────────────────────────────────────────

    def start(self, bind_timeout_s: float = 5.0, interval_s: float = 0.05) -> str:
        """Send the request and bind to the server task that runs it. Returns the binding state."""
        try:
            state = self._probe.read()
        except ProbeError as exc:
            raise RequestRefused(f"cannot verify an idle child before sending: {exc}") from exc
        if not state.idle:
            raise RequestRefused("child is not resident and idle; request-scoped cancel needs a "
                                 "single-flight child")
        self._worker = spawn_context_thread(self._run, name="scoped-llamacpp-request")
        self._worker.start()
        deadline = time.monotonic() + bind_timeout_s
        while time.monotonic() < deadline and not self._finished.is_set():
            try:
                busy = self._probe.read().busy_slots
            except ProbeError:
                busy = ()
            if len(busy) == 1:
                self.bound_slot, self.bound_task = busy[0]
                self.binding = "bound"
                return self.binding
            if len(busy) > 1:
                self.binding = "ambiguous"
                return self.binding
            self._finished.wait(interval_s)
        if self._finished.is_set() and self._response is not None:
            self.binding = "completed_before_bind"
        return self.binding

    def _run(self) -> None:
        conn = http.client.HTTPConnection(self._probe.host, self._probe.port,
                                          timeout=self._request_timeout_s)
        with self._lock:
            self._conn = conn
        try:
            conn.request("POST", "/v1/chat/completions", body=self._payload, headers={
                "Authorization": f"Bearer {self._probe.api_key}",
                "Content-Type": "application/json"})
            resp = conn.getresponse()
            body = resp.read()  # IncompleteRead on a truncated body: never a "completed" response
            self._response = (resp.status, body)
        except (OSError, http.client.HTTPException) as exc:
            self._error = exc
        finally:
            self._finished.set()

    def wait(self, timeout_s: float | None = None) -> tuple[int, bytes] | None:
        """The full response, or None if cancelled/failed/timed out (see ``error``)."""
        self._finished.wait(timeout_s)
        return self._response

    @property
    def error(self) -> BaseException | None:
        return self._error

    # ── cancellation ─────────────────────────────────────

    def cancel(self, timeout_s: float = 30.0, *, settle_reads: int = 2,
               interval_s: float = 0.25) -> CancelResult:
        """Close the request's connection, then prove the server stopped. Idempotent.

        ``cancelled`` is True only after ``settle_reads`` consecutive clean idle reads. A task the
        server queues after the final clean read and before it notices the closed socket is the
        one race this cannot exclude; ``settle_reads * interval_s`` is how long the server had to
        show it. A response that fully arrived before the close reports COMPLETED_BEFORE_CANCEL.
        """
        with self._lock:
            if self._result is not None:
                return self._result
            conn = self._conn
        started = time.monotonic()
        if conn is not None and conn.sock is not None:
            try:
                conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        self._finished.wait(min(timeout_s, 5.0))
        if self._response is not None:
            return self._remember(CancelResult(
                CancelOutcome.COMPLETED_BEFORE_CANCEL,
                "full response was received before the connection closed", self.bound_slot,
                self.bound_task, 0, time.monotonic() - started))
        result = self._verify(timeout_s - (time.monotonic() - started), settle_reads, interval_s,
                              started)
        return self._remember(result)

    def _remember(self, result: CancelResult) -> CancelResult:
        with self._lock:
            self._result = result
        return result

    def _verify(self, budget_s: float, settle_reads: int, interval_s: float,
                started: float) -> CancelResult:
        deadline = time.monotonic() + max(0.0, budget_s)
        clean = 0
        last_problem = "no read completed"
        problem_kind = CancelOutcome.UNVERIFIABLE
        while True:
            try:
                state = self._probe.read()
                if not state.resident:
                    clean, last_problem = 0, "model child is not resident"
                    problem_kind = CancelOutcome.MODEL_NOT_RESIDENT
                elif state.idle and not any(task == self.bound_task and self.bound_task is not None
                                            for _, task in state.busy_slots):
                    clean += 1
                    if clean >= settle_reads:
                        return CancelResult(CancelOutcome.CONFIRMED_CANCELLED,
                                            "connection closed; slots, metrics and queue idle",
                                            self.bound_slot, self.bound_task, clean,
                                            time.monotonic() - started)
                else:
                    clean = 0
                    last_problem = (f"still busy: slots={list(state.busy_slots)} "
                                    f"processing={state.requests_processing} "
                                    f"deferred={state.requests_deferred}")
                    problem_kind = CancelOutcome.NOT_CONFIRMED
            except ProbeError as exc:
                clean, last_problem = 0, f"telemetry unreadable: {exc}"
                problem_kind = CancelOutcome.UNVERIFIABLE
            if time.monotonic() >= deadline:
                # A response that completed while we were verifying is a completion, not a cancel.
                if self._response is not None:
                    return CancelResult(CancelOutcome.COMPLETED_BEFORE_CANCEL,
                                        "full response arrived during verification",
                                        self.bound_slot, self.bound_task, clean,
                                        time.monotonic() - started)
                return CancelResult(problem_kind, last_problem, self.bound_slot, self.bound_task,
                                    clean, time.monotonic() - started)
            time.sleep(interval_s)

    def evict(self, timeout_s: float = 60.0, interval_s: float = 0.25) -> CancelResult:
        """Last resort after NOT_CONFIRMED: unload the child (frees its memory, loses residency)
        and confirm via /models that it left. Reported as EVICTED, never as a clean cancel."""
        started = time.monotonic()
        try:
            self._probe.unload()
        except ProbeError as exc:
            return CancelResult(CancelOutcome.UNVERIFIABLE, f"unload not accepted: {exc}",
                                self.bound_slot, self.bound_task, 0, time.monotonic() - started)
        deadline = started + timeout_s
        while time.monotonic() < deadline:
            try:
                if self._probe.model_status() not in (*_RESIDENT, "unloading"):
                    return CancelResult(CancelOutcome.EVICTED, "model child unloaded",
                                        self.bound_slot, self.bound_task, 0,
                                        time.monotonic() - started)
            except ProbeError:
                pass
            time.sleep(interval_s)
        return CancelResult(CancelOutcome.NOT_CONFIRMED, "child still resident after unload",
                            self.bound_slot, self.bound_task, 0, time.monotonic() - started)
