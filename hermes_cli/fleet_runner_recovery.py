"""Opt-in runner recovery. Resolve may seal; it never sends another claim POST."""

import hashlib
import json
import logging
import secrets
import sqlite3
import subprocess
import time

from hermes_cli.fleet_claim_journal import ClaimJournal, _claim_identity
from hermes_cli.fleet_client import FleetTransportError
from hermes_cli.fleet_execution import ClaimGuard
from hermes_cli.fleet_protocol import (
    CLAIM_RECOVERY_VERSION,
    ClaimOutcome,
    ClaimRequest,
    ProtocolError,
)
from hermes_cli.fleet_store import TaskClaim


def _held():
    logging.getLogger(__name__).warning(
        "Fleet recovery held; retained intent requires exact reconciliation"
    )
    return False


class RecoveryClaimGuard(ClaimGuard):
    """Preserve legacy guard behavior while validating every recovered renewal."""

    def __init__(self, runner, claim, now, interval, request):
        super().__init__(runner, claim, now, interval)
        self.request = request

    def refresh(self):
        try:
            if self.runner.stopped or not self.runner._is_live():
                self.error = "owning Hermes stopped"
                self.cancel.set()
                return
            claim = self.runner.coordinator.renew_claim(
                self.claim.task_id, self.claim.claim_id, now=self.now()
            )
            if claim is None:
                raise ProtocolError("claim renewal refused")
            from dataclasses import asdict

            ClaimOutcome(self.request, "claimed", asdict(claim))
            if (
                _claim_identity(claim) != _claim_identity(self.claim)
                or claim.lease_expires_at <= self.now()
            ):
                raise ProtocolError("exact recovered claim renewal refused")
            for capability in self.runner.capabilities:
                if not self.runner.coordinator.heartbeat_runner(
                    capability, now=self.now(), ttl=self.runner._runner_ttl
                ):
                    raise ProtocolError("runner heartbeat refused")
        except (
            OSError,
            RuntimeError,
            sqlite3.Error,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ):
            self.error = "exact recovered claim maintenance unavailable"
            self.cancel.set()


def prelaunch_refusal(runner, guard):
    """Recheck ownership after the durable execution fence, before external I/O."""
    if guard is not None and not guard.cancel.is_set() and not runner.stopped and runner._is_live():
        return None
    result = subprocess.CompletedProcess([], 75, "", "Recovery owner/lease lost before launch")
    result.not_started = True
    result.outcome_unknown = False
    result.telemetry = None
    return result


def _journal(runner):
    if runner._claim_journal is None:
        runner._claim_journal = ClaimJournal(
            runner.state_dir, node_id=runner.node_id, coordinator=runner.coordinator.base_url
        )
    journal = runner._claim_journal
    info = runner.coordinator.claim_recovery_capabilities()
    journal.bind(info)
    return journal, info


def _record(runner, claim):
    record = runner._find_task(claim.task_id)
    if record is None or _claim_identity(record) != _claim_identity(claim):
        raise ProtocolError("current coordinator claim identity changed")
    return record


def _resolve(runner, journal, active):
    req = active["request"]
    if req.capability not in runner.capabilities:
        raise ProtocolError("pending claim capability changed")
    if active["stage"] == "intent":
        outcome = runner.coordinator.lookup_claim_request(req)
        outcome = ClaimOutcome.from_dict(outcome.to_dict(), request=req)
        if outcome.kind == "absent":
            outcome = runner.coordinator.resolve_claim_request(req)
            outcome = ClaimOutcome.from_dict(outcome.to_dict(), request=req)
        journal.record(outcome)
        if outcome.kind != "claimed":
            return None
        active = journal.active()
    return active


def _execution_evidence(runner, claim):
    directory = runner._capture_root() / hashlib.sha256(claim.claim_id.encode()).hexdigest()
    path = directory / "claim.json"
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if (
            value.get("claim_id") != claim.claim_id
            or value.get("task_id") != claim.task_id
            or value.get("node_id") != claim.node_id
            or value.get("profile") != claim.runner_profile
        ):
            raise ProtocolError("claim capture identity mismatch")
        if value.get("stage") != "claimed":
            return True
    return any(directory.glob("stdout-*")) or any(directory.glob("stderr-*"))


def _settle_delivery(runner, journal, claim, payload, *, now=None):
    saved = claim if isinstance(claim, dict) else claim.__dict__
    if any(payload[key] != saved[key] for key in ("task_id", "claim_id")):
        raise ProtocolError("delivery does not own current intent")
    if not runner._send_delivery(payload, now=now, retain_proof=True):
        return False
    # A failed local commit must leave the exact acknowledged delivery available
    # for restart. Cleanup leftovers after this commit are idempotently replayed.
    journal.finish(claim)
    runner._remove_capture(payload["claim_id"])
    runner._delivery_path(payload).unlink(missing_ok=True)
    return True


def _recover_execution(runner, journal, claim, record):
    from hermes_cli.fleet_execution import stream_event

    directory = runner._capture_root() / hashlib.sha256(claim.claim_id.encode()).hexdigest()
    stdout = "\n".join(
        path.read_text(encoding="utf-8-sig", errors="replace")
        for path in sorted(directory.glob("stdout-*"))
    )
    identity = stream_event(stdout, "system", "admission")
    if identity is None:
        return _held()
    original = subprocess.CompletedProcess([], 75, stdout, "Retained original execution")
    result = runner._reconcile(claim.runner_profile, identity, cancel=False, original=original)
    terminal = stream_event(result.stdout or "", "result")
    if terminal is None or terminal.get("settled") is not True:
        return _held()
    if record.status in {"completed", "failed", "cancelled"}:
        journal.finish(claim)
        runner._remove_capture(claim.claim_id)
        return False
    from hermes_cli.fleet_runner import _failure_diagnostic, _parse_stream_json_result

    text, telemetry = _parse_stream_json_result(result.stdout or "")
    payload = {
        "task_id": claim.task_id,
        "claim_id": claim.claim_id,
        "status": "completed" if result.returncode == 0 else "failed",
        "result": text,
        "error": _failure_diagnostic(result),
        "telemetry": telemetry,
    }
    runner._save_delivery(payload)
    _settle_delivery(runner, journal, claim, payload)
    return False


def _execute_claim(runner, journal, active, *, now):
    claim = TaskClaim(**active["outcome"].claim)
    record = _record(runner, claim)
    if active["stage"] == "executing":
        return _recover_execution(runner, journal, claim, record)
    if _execution_evidence(runner, claim):
        # Existing admission/capture evidence outranks a prelaunch-looking row.
        # Persist the stronger fence before observing the original admission.
        journal.mark_executing(claim)
        return _recover_execution(runner, journal, claim, record)
    if record.status in {"completed", "failed", "cancelled"}:
        journal.finish(claim)
        return False
    observed = time.time() if now is None else now
    if (
        record.status != "running"
        or record.lease_expires_at is None
        or record.lease_expires_at <= observed
    ):
        return _held()
    renewed = runner.coordinator.renew_claim(claim.task_id, claim.claim_id, now=now)
    if renewed is None or _claim_identity(renewed) != _claim_identity(claim):
        return _held()
    from hermes_cli.fleet_runner import _failure_diagnostic, _LEASE_REFRESH_SECONDS

    runner._journal_claim(claim, stage="claimed")
    with RecoveryClaimGuard(runner, claim, now, _LEASE_REFRESH_SECONDS, active["request"]) as guard:
        if guard.cancel.is_set():
            return _held()
        result = runner._execute_task(record, claim.runner_profile, guard=guard, claim=claim)
        if getattr(result, "outcome_unknown", False):
            runner.coordinator.mark_uncertain(
                claim.task_id,
                claim.claim_id,
                result=result.stdout or "",
                error=result.stderr or "Unknown original admission",
                now=guard.now(),
            )
            return _held()
        payload = {
            "task_id": claim.task_id,
            "claim_id": claim.claim_id,
            "status": "completed" if result.returncode == 0 else "failed",
            "result": (result.stdout or "").strip(),
            "error": _failure_diagnostic(result),
            "telemetry": getattr(result, "telemetry", None),
        }
        runner._save_delivery(payload)
        if not _settle_delivery(runner, journal, claim, payload, now=guard.now()):
            return _held()
        return True


def _poll(runner, *, now):
    if any(runner.state_dir.glob("*/claim.json")) or any(runner.state_dir.glob("*.delivery.json")):
        raise ProtocolError("legacy state requires explicit reconciliation before cutover")
    journal, info = _journal(runner)
    active = journal.active()
    if active is not None:
        # A durable terminal delivery predates its ACK; replaying that exact
        # idempotent settlement takes precedence over any request reconciliation.
        claim = active["outcome"].claim if active["outcome"] is not None else None
        delivery = None if claim is None else runner._delivery_path(claim)
        if delivery is not None and delivery.exists():
            payload = json.loads(delivery.read_text(encoding="utf-8-sig"))
            if not _settle_delivery(runner, journal, claim, payload):
                return _held()
            return False
        active = _resolve(runner, journal, active)
        return False if active is None else _execute_claim(runner, journal, active, now=now)
    if not runner._deliver_pending() or not runner._recover_executions():
        return _held()
    for capability in runner.capabilities:
        if runner.stopped or not runner._is_live():
            return False
        if not runner.coordinator.heartbeat_runner(capability, now=now, ttl=runner._runner_ttl):
            return _held()
        req = ClaimRequest(
            CLAIM_RECOVERY_VERSION,
            info["store_id"],
            info["cutover"]["cutover_id"],
            journal.installation_id,
            secrets.token_hex(32),
            capability,
            now,
            60.0,
        )
        journal.begin(req)  # commit completes before the only claim send
        outcome = runner.coordinator.claim_request(req)
        outcome = ClaimOutcome.from_dict(outcome.to_dict(), request=req)
        journal.record(outcome)
        if outcome.kind == "claimed":
            return _execute_claim(runner, journal, journal.active(), now=now)
    return False


def run_once(runner, *, now):
    if not runner._recovery_poll_lock.acquire(blocking=False):
        return _held()
    try:
        return _poll(runner, now=now)
    except (
        FleetTransportError,
        OSError,
        sqlite3.Error,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ):
        # No failure clears the intent or selects another capability. Deliberately
        # do not log exception bodies, which may contain transport credentials.
        return _held()
    finally:
        runner._recovery_poll_lock.release()
