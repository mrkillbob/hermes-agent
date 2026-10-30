"""Typed stdlib HTTP client for the federated fleet coordinator."""

from __future__ import annotations

import errno
import http.client
import json
import urllib.error
import urllib.request
from typing import Any

from hermes_cli.fleet_protocol import (
    FleetTask,
    MAX_MESSAGE_BYTES,
    RunnerCapability,
    RunnerTelemetry,
    TaskRequirement,
    TaskTelemetry,
    encode_message,
)
from hermes_cli.fleet_store import TaskClaim, TaskRecord
from hermes_cli.urllib_security import open_credentialed_url


class FleetTransportError(RuntimeError):
    """Raised for an unavailable or rejected coordinator request."""


class FleetTransientTransportError(FleetTransportError):
    """A transport outage; only explicitly idempotent callers may retry."""


def _is_transient_transport_failure(error: BaseException) -> bool:
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    return isinstance(cause, (TimeoutError, ConnectionError)) or (
        isinstance(cause, OSError) and cause.errno in {
            errno.ETIMEDOUT, errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED,
            errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EPIPE,
        })


class FleetClient:
    def __init__(self, base_url: str, *, token: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None,
                 *, response_limit: int | None = None) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=payload,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                **({"Content-Type": "application/json"} if payload is not None else {}),
            },
        )
        try:
            with open_credentialed_url(request, timeout=self.timeout) as response:
                if response_limit is None:
                    raw = response.read()
                else:
                    declared = response.headers.get("Content-Length", "")
                    if not declared.isascii() or not declared.isdigit() or int(declared) > response_limit:
                        raise FleetTransportError("unbounded recovery response framing")
                    raw = response.read(int(declared) + 1)
                    if len(raw) != int(declared):
                        raise FleetTransportError("incomplete recovery response framing")
                decoded = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise FleetTransportError(f"{exc.code}: coordinator rejected request") from exc
        except (urllib.error.URLError, OSError, http.client.HTTPException, UnicodeError, RecursionError, json.JSONDecodeError) as exc:
            error_type = FleetTransientTransportError if _is_transient_transport_failure(exc) else FleetTransportError
            raise error_type(f"coordinator request failed: {exc}") from exc
        if not isinstance(decoded, dict):
            raise FleetTransportError("coordinator returned a non-object response")
        return decoded

    @staticmethod
    def _message(value) -> dict[str, Any]:
        return json.loads(encode_message(value).decode("utf-8"))

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/fleet/health")

    def register_runner(self, capability: RunnerCapability, *, now: float | None = None, ttl: float = 30.0) -> bool:
        return bool(
            self._request(
                "POST",
                "/v1/fleet/runners/register",
                {"message": self._message(capability), "now": now, "ttl": ttl},
            ).get("ok")
        )

    def heartbeat_runner(
        self,
        capability: RunnerCapability,
        *,
        telemetry: RunnerTelemetry | None = None,
        now: float | None = None,
        ttl: float = 30.0,
    ) -> bool:
        return bool(
            self._request(
                "POST",
                f"/v1/fleet/runners/{capability.node_id}/{capability.profile}/heartbeat",
                {"now": now, "ttl": ttl, "telemetry": telemetry.to_dict() if telemetry else None},
            ).get("ok")
        )

    def submit_task(self, task: FleetTask, *, now: float | None = None) -> TaskRecord:
        return _record_from_dict(self._request(
            "POST", "/v1/fleet/tasks", {"message": self._message(task), "now": now}
        ))

    def _recovery_request(self, method, action, body=None):
        return self._request(method, "/v2/fleet/claim-recovery/" + action, body,
                             response_limit=MAX_MESSAGE_BYTES)

    def claim_recovery_capabilities(self):
        from hermes_cli.fleet_protocol import validate_recovery_capabilities
        return validate_recovery_capabilities(self._recovery_request("GET", "capabilities"))

    def establish_claim_cutover(self, expected_store_id):
        """Explicit coordinator-wide cutover; never called by runner recovery."""
        from hermes_cli.fleet_protocol import validate_recovery_capabilities
        return validate_recovery_capabilities(self._recovery_request("POST", "cutover",
                                                            {"expected_store_id": expected_store_id}))

    def claim_request(self, request):
        from hermes_cli.fleet_protocol import ClaimOutcome, ProtocolError
        outcome = ClaimOutcome.from_dict(self._recovery_request("POST", "claim", request.to_dict()), request=request)
        if outcome.kind == "absent":
            raise ProtocolError("claim returned absent outcome")
        return outcome

    def lookup_claim_request(self, request):
        from urllib.parse import quote
        from hermes_cli.fleet_protocol import ClaimOutcome
        binding = quote(json.dumps(request.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False), safe="")
        return ClaimOutcome.from_dict(self._recovery_request("GET", "outcome?request=" + binding), request=request)

    def resolve_claim_request(self, request):
        from hermes_cli.fleet_protocol import ClaimOutcome, ProtocolError
        outcome = ClaimOutcome.from_dict(self._recovery_request("POST", "resolve", request.to_dict()), request=request)
        if outcome.kind == "absent":
            raise ProtocolError("resolve returned absent outcome")
        return outcome

    def claim_task(
        self,
        capability: RunnerCapability,
        *,
        now: float | None = None,
        lease_seconds: float = 60.0,
    ) -> TaskClaim | None:
        payload = self._request(
            "POST",
            "/v1/fleet/tasks/claim",
            {
                "capability": capability.to_dict(),
                "now": now,
                "lease_seconds": lease_seconds,
            },
        )
        if "claim" not in payload or (payload["claim"] is not None and not isinstance(payload["claim"], dict)):
            raise FleetTransportError("coordinator returned an invalid claim envelope")
        claim = payload["claim"]
        try:
            return TaskClaim(**claim) if claim is not None else None
        except TypeError as exc:
            raise FleetTransportError("coordinator returned an incomplete claim") from exc

    def renew_claim(
        self, task_id: str, claim_id: str, *, now: float | None = None, lease_seconds: float = 60.0
    ) -> TaskClaim | None:
        payload = self._request(
            "POST",
            f"/v1/fleet/tasks/{task_id}/renew",
            {"claim_id": claim_id, "now": now, "lease_seconds": lease_seconds},
        )
        if "claim" not in payload or (payload["claim"] is not None and not isinstance(payload["claim"], dict)):
            raise FleetTransportError("coordinator returned an invalid claim envelope")
        claim = payload["claim"]
        try:
            return TaskClaim(**claim) if claim is not None else None
        except TypeError as exc:
            raise FleetTransportError("coordinator returned an incomplete claim") from exc

    def complete_task(
        self,
        task_id: str,
        claim_id: str,
        *,
        result: str = "",
        telemetry: TaskTelemetry | None = None,
        now: float | None = None,
    ) -> bool:
        return bool(
            self._request(
                "POST",
                f"/v1/fleet/tasks/{task_id}/complete",
                {
                    "claim_id": claim_id,
                    "result": result,
                    "telemetry": telemetry.to_dict() if telemetry else None,
                    "now": now,
                },
            ).get("ok")
        )

    def fail_task(self, task_id: str, claim_id: str, *, error: str, now: float | None = None) -> bool:
        return bool(
            self._request(
                "POST",
                f"/v1/fleet/tasks/{task_id}/fail",
                {"claim_id": claim_id, "error": error, "now": now},
            ).get("ok")
        )

    def mark_uncertain(self, task_id: str, claim_id: str, *, error: str, result: str = "", now: float | None = None) -> bool:
        return bool(self._request("POST", f"/v1/fleet/tasks/{task_id}/uncertain",
                                 {"claim_id": claim_id, "error": error, "result": result, "now": now}).get("ok"))

    def retry_task(self, task_id: str, *, now: float | None = None) -> bool:
        return bool(self._request("POST", f"/v1/fleet/tasks/{task_id}/retry", {"now": now}).get("ok"))

    def cancel_task(self, task_id: str, *, now: float | None = None) -> bool:
        return bool(self._request("POST", f"/v1/fleet/tasks/{task_id}/cancel", {"now": now}).get("ok"))

    def list_tasks(self) -> list[TaskRecord]:
        return [_record_from_dict(item) for item in self._request("GET", "/v1/fleet/tasks").get("tasks", [])]

    def list_runners(self) -> list[dict[str, Any]]:
        """Return coordinator-owned runner status without exposing the token."""
        runners = self._request("GET", "/v1/fleet/runners").get("runners", [])
        return [runner for runner in runners if isinstance(runner, dict)]


def _record_from_dict(value: dict[str, Any]) -> TaskRecord:
    return TaskRecord(
        task_id=value["task_id"],
        title=value["title"],
        body=value["body"],
        requirement=TaskRequirement.from_dict(value["requirement"]),
        idempotency_key=value["idempotency_key"],
        status=value["status"],
        claim_id=value.get("claim_id"),
        node_id=value.get("node_id"),
        runner_profile=value.get("runner_profile"),
        lease_expires_at=value.get("lease_expires_at"),
        attempt=value["attempt"],
        result=value.get("result"),
        error=value.get("error"),
        created_at=value["created_at"],
        updated_at=value["updated_at"],
        output_tokens=value.get("output_tokens"),
        duration_ms=value.get("duration_ms"),
        output_tps=value.get("output_tps"),
    )
