"""Compatibility runner that delegates execution to a local stock Hermes CLI."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from hermes_cli.fleet_protocol import RunnerCapability, TaskTelemetry

# Maintenance cadence is independent of the task duration.
_LEASE_REFRESH_SECONDS = 10.0


def _parse_stream_json_result(stdout: str) -> tuple[str, TaskTelemetry | None]:
    """Extract the authoritative one-shot result without scraping human output."""
    terminal: dict[str, Any] | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            terminal = event

    if terminal is None:
        return stdout.strip(), None

    tokens = terminal.get("tokens")
    if not isinstance(tokens, dict):
        return str(terminal.get("text") or ""), None
    try:
        telemetry = TaskTelemetry(
            output_tokens=tokens.get("output", 0),
            duration_ms=terminal.get("duration_ms", 0),
        )
    except (TypeError, ValueError):
        telemetry = None
    return str(terminal.get("text") or ""), telemetry


def _failure_diagnostic(result) -> str:
    """Keep authoritative failure details ahead of the CLI's stderr session footer."""
    from hermes_cli.fleet_execution import stream_event

    stdout = result.stdout or ""
    terminal = stream_event(stdout, "result") or {}
    identity = stream_event(stdout, "system", "admission") or {}
    details = [str(terminal.get(key) or "").strip() for key in ("error", "text")]
    for key in ("session_id", "admission_id"):
        value = terminal.get(key) or identity.get(key)
        if value:
            details.append(f"{key}: {value}")
    details.append((result.stderr or "").strip())
    if not terminal:
        details.append(stdout.strip())
    return "\n".join(dict.fromkeys(detail for detail in details if detail)) or "Hermes exited with a failure"


class FleetRunner:
    """Claim coordinator work only while the owning local Hermes is live.

    ``coordinator`` is intentionally duck-typed so the runner can use the
    normal HTTP client in production and a deterministic fake in tests.
    """

    def __init__(
        self,
        node_id: str,
        coordinator: Any,
        hermes_executable: str,
        profiles: Iterable[str],
        projects: Iterable[str],
        *,
        capabilities: Iterable[RunnerCapability] | None = None,
        models: Iterable[str] = (),
        tools: Iterable[str] = ("terminal", "git"),
        profile_models: Mapping[str, str] | None = None,
        profile_providers: Mapping[str, str] | None = None,
        liveness_check: Callable[[], bool] | None = None,
        executor: Callable[[list[str], str], Any] | None = None,
        state_dir: str | Path | None = None,
    ):
        self.node_id = node_id
        self.coordinator = coordinator
        self.hermes_executable = hermes_executable
        self.profiles = tuple(profiles)
        self.projects = tuple(projects)
        self.profile_models = dict(profile_models or {})
        self.profile_providers = dict(profile_providers or {})
        self.capabilities = tuple(capabilities or self._default_capabilities(models, tools))
        # Each sweep performs registration, heartbeat and claim requests per profile.
        # A large fleet must not expire its early profiles before the sweep finishes.
        self._runner_ttl = max(30.0, 3.0 * len(self.capabilities))
        self._liveness_check = liveness_check or (lambda: True)
        self._executor = executor
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self._stop = threading.Event()
        self._delivered: dict[str, str] = {}
        self._results: dict[str, str] = {}

    def _default_capabilities(self, models: Iterable[str], tools: Iterable[str]):
        current_platform = "windows" if platform.system().lower() == "windows" else platform.system().lower()
        default_models = tuple(models)
        return tuple(
            RunnerCapability(
                node_id=self.node_id,
                profile=profile,
                models=((self.profile_models[profile],) if profile in self.profile_models else default_models),
                tools=tuple(tools),
                projects=self.projects,
                platform=current_platform,
            )
            for profile in self.profiles
        )

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()

    def register(self) -> bool:
        if not self._is_live():
            return False
        for capability in self.capabilities:
            self.coordinator.register_runner(capability, ttl=self._runner_ttl)
        return True

    def run_once(self, *, now: float | None = None) -> bool:
        if self.stopped or not self._is_live():
            return False
        if not self._deliver_pending() or not self._recover_executions():
            return False
        claimed = False
        for capability in self.capabilities:
            self.coordinator.heartbeat_runner(capability, now=now, ttl=self._runner_ttl)
            claim = self.coordinator.claim_task(capability, now=now)
            if claim is None:
                continue
            claimed = True
            self._journal_claim(claim, stage="claimed")
            task = self._find_task(claim.task_id)
            if task is None:
                payload = {"task_id": claim.task_id, "claim_id": claim.claim_id, "status": "failed",
                           "result": "", "error": "coordinator returned an unknown task", "telemetry": None}
                self._save_delivery(payload)
                if not self._send_delivery(payload, now=now):
                    self.stop()
                    break
                continue
            if claim.task_id in self._delivered:
                self.coordinator.complete_task(
                    claim.task_id,
                    claim.claim_id,
                    result=self._results.get(claim.task_id, "duplicate delivery"),
                    now=now,
                )
                continue
            from hermes_cli.fleet_execution import ClaimGuard
            with ClaimGuard(self, claim, now, _LEASE_REFRESH_SECONDS) as guard:
                if guard.cancel.is_set():
                    # No work has been launched; the exact claim may already be gone.
                    self.stop()
                    break
                result = self._execute_task(task, claim.runner_profile, guard=guard, claim=claim)
                if getattr(result, "outcome_unknown", False):
                    self.coordinator.mark_uncertain(
                        claim.task_id, claim.claim_id, result=result.stdout or "",
                        error=result.stderr or "Gateway outcome unknown; reconcile before retry", now=guard.now())
                    self.stop()
                    break
                payload = {"task_id": claim.task_id, "claim_id": claim.claim_id,
                           "status": "completed" if result.returncode == 0 else "failed",
                           "result": (result.stdout or "").strip(),
                           "error": _failure_diagnostic(result),
                           "telemetry": getattr(result, "telemetry", None)}
                self._save_delivery(payload)
                if not self._send_delivery(payload, now=guard.now()):
                    self.stop()
                    break
                if result.returncode == 0:
                    self._results[claim.task_id] = payload["result"]
                    self._delivered[claim.task_id] = claim.claim_id
            if self.stopped:
                break
        return claimed

    def run_forever(self, *, interval: float = 5.0) -> None:
        while not self.stopped:
            if self._is_live():
                self.register()
                self.run_once()
            self._stop.wait(interval)

    def _is_live(self) -> bool:
        return bool(self._liveness_check())

    def _find_task(self, task_id: str):
        for task in self.coordinator.list_tasks():
            candidate_id = task.get("task_id") if isinstance(task, dict) else task.task_id
            if candidate_id == task_id:
                return task
        return None

    def _execute_task(self, task: Any, profile: str, *, guard=None, claim=None):
        body = task.get("body") if isinstance(task, dict) else task.body
        requirement = task.get("requirement") if isinstance(task, dict) else task.requirement
        models = requirement.get("models", ()) if isinstance(requirement, dict) else requirement.models
        argv = [self.hermes_executable, "-p", profile, "chat"]
        if len(models) == 1:
            provider = self.profile_providers.get(profile)
            if provider:
                argv.extend(["--provider", provider])
            argv.extend(["-m", models[0]])
        # Keep task data out of the command line, including Windows batch launchers.
        argv.extend(["--query-file", "-", "--format", "stream-json"])
        output_dir = self._journal_claim(claim, stage="executing") if claim is not None else None
        if self._executor is not None:
            result = self._executor(argv, body)
        else:
            from hermes_cli.fleet_execution import profile_child_env
            try:
                env = profile_child_env(profile)
            except (OSError, ValueError) as exc:
                result = subprocess.CompletedProcess(argv, 127, "",
                    f"Fleet profile environment unavailable ({type(exc).__name__})")
                result.not_started = True
            else:
                result = self._execute(argv, body, cancel=guard.cancel if guard else None,
                                       output_dir=output_dir, env=env)
        from hermes_cli.fleet_execution import stream_event
        terminal = stream_event(result.stdout or "", "result")
        # CLI failures and detach receipts can leave real gateway work alive. Only an
        # exact terminal admission receipt can turn that into a safe failure/retry.
        if not getattr(result, "not_started", False) and (
                terminal is None or result.returncode != 0 or terminal.get("exit_code", 0) != 0):
            identity = stream_event(result.stdout or "", "system", "admission")
            if (identity is None and terminal is not None and not terminal.get("admission_id")
                    and terminal.get("admission_attempted") is False
                    and "settled" not in terminal and terminal.get("exit_code", 0) != 0
                    and not getattr(result, "transport_interrupted", False)):
                # A terminal CLI rejection before admission owns no gateway work.
                result.outcome_unknown = False
                result.telemetry = None
                return result
            if identity is not None:
                result = self._reconcile(profile, identity, cancel=bool(guard and guard.cancel.is_set()), original=result, guard=guard)
                terminal = stream_event(result.stdout or "", "result")
            result.outcome_unknown = terminal is None or terminal.get("settled") is not True
        else:
            result.outcome_unknown = False
        if not result.outcome_unknown and result.returncode == 0:
            result.stdout, result.telemetry = _parse_stream_json_result(result.stdout or "")
        else:
            result.telemetry = None
        return result

    def _reconcile(self, profile, identity, *, cancel, original, guard=None):
        from hermes_cli._subprocess_compat import bounded_probe_run
        from hermes_cli.fleet_execution import profile_child_env, stream_event
        try:
            env = profile_child_env(profile)
        except (OSError, ValueError) as exc:
            original.stderr = (original.stderr or "") + f"\nRecovery profile unavailable ({type(exc).__name__}); do not retry"
            return original
        while True:
            argv = [self.hermes_executable, "-p", profile, "fleet", "reconcile",
                    "--session-id", identity["session_id"], "--admission-id", identity["admission_id"]]
            stopping = cancel or bool(guard and guard.cancel.is_set())
            if stopping:
                argv.append("--cancel")
            result = bounded_probe_run(argv, timeout=30, env=env)
            terminal = stream_event(result.stdout or "", "result") if result is not None else None
            if terminal is not None and terminal.get("settled") is True:
                return result
            if (terminal is None or terminal.get("admission_status") not in {"queued", "started"}
                    or terminal.get("needs_input") or stopping or guard is None):
                original.stderr = (original.stderr or "") + ("\nGateway outcome unknown; exact session/admission: "
                    + identity["session_id"] + "/" + identity["admission_id"] + "; do not retry")
                return original
            # The CLI transport ended, but the authoritative turn is still alive.
            # Continue reading that admission while its original claim is renewed.
            guard.cancel.wait(2)

    def _journal_claim(self, claim, *, stage):
        if self.state_dir is None:
            return None
        directory = self.state_dir / hashlib.sha256(claim.claim_id.encode()).hexdigest()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._write_receipt(directory / "claim.json", {
            "task_id": claim.task_id, "claim_id": claim.claim_id, "profile": claim.runner_profile,
            "node_id": self.node_id, "stage": stage})
        return directory

    def _remove_capture(self, claim_id):
        """Remove only this acknowledged claim's owned files; retain other evidence."""
        directory = self.state_dir / hashlib.sha256(claim_id.encode()).hexdigest()
        for pattern in ("stdout-*", "stderr-*", "input-*", "claim.json"):
            for path in directory.glob(pattern):
                path.unlink()
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()

    def _delivery_path(self, payload):
        return self.state_dir / (hashlib.sha256(payload["claim_id"].encode()).hexdigest() + ".delivery.json")

    def _save_delivery(self, payload):
        if self.state_dir is None:
            return
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = {**payload, "telemetry": payload["telemetry"].to_dict() if payload["telemetry"] else None}
        path = self._delivery_path(payload)
        self._write_receipt(path, data)

    @staticmethod
    def _write_receipt(path, data):
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            temporary.chmod(0o600)
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)

    def _send_delivery(self, payload, *, now=None):
        if payload["status"] == "completed":
            telemetry = payload.get("telemetry")
            if isinstance(telemetry, dict):
                telemetry = TaskTelemetry.from_dict(telemetry)
            ok = self.coordinator.complete_task(payload["task_id"], payload["claim_id"],
                result=payload["result"], telemetry=telemetry, now=now)
        else:
            ok = self.coordinator.fail_task(payload["task_id"], payload["claim_id"], error=payload["error"], now=now)
        if ok and self.state_dir is not None:
            self._remove_capture(payload["claim_id"])
            self._delivery_path(payload).unlink(missing_ok=True)
        return ok

    def _deliver_pending(self):
        if self.state_dir is None or not self.state_dir.exists():
            return True
        for path in self.state_dir.glob("*.delivery.json"):
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not self._send_delivery(payload):
                self.stop()
                return False
        return True

    def _recover_executions(self):
        """A retained claim is reconciled, never submitted again after a runner crash."""
        if self.state_dir is None or not self.state_dir.exists():
            return True
        journals = list(self.state_dir.glob("*/claim.json"))
        if not journals:
            return True
        records = {row.task_id: row for row in self.coordinator.list_tasks()}
        from hermes_cli.fleet_execution import stream_event
        for path in journals:
            try:
                saved = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(saved, dict) or not all(isinstance(saved.get(key), str)
                        for key in ("task_id", "claim_id", "profile", "node_id")):
                    raise ValueError("Invalid claim receipt")
            except (ValueError, OSError):
                # Match a damaged journal back to the coordinator's exact original
                # claim; never infer that missing local data authorizes a replay.
                record = next((row for row in records.values() if row.claim_id and row.node_id == self.node_id
                    and hashlib.sha256(row.claim_id.encode()).hexdigest() == path.parent.name), None)
                if record is not None:
                    self.coordinator.mark_uncertain(record.task_id, record.claim_id,
                        error="Damaged local claim receipt; inspect retained capture before retry")
                self.stop()
                return False
            record = records.get(saved["task_id"])
            if (record is None or record.claim_id != saved["claim_id"] or record.node_id != self.node_id
                    or record.runner_profile != saved["profile"]):
                self.stop()
                return False
            if record.status in {"completed", "failed", "cancelled"}:
                self._remove_capture(record.claim_id)
                continue
            if saved.get("stage") == "claimed":
                payload = {"task_id": record.task_id, "claim_id": record.claim_id, "status": "failed",
                           "result": "", "error": "Runner restarted before launching task", "telemetry": None}
                self._save_delivery(payload)
                if not self._send_delivery(payload):
                    self.stop()
                    return False
                continue
            captures = sorted(path.parent.glob("stdout-*"))
            stdout = "\n".join(capture.read_text(encoding="utf-8-sig", errors="replace") for capture in captures)
            identity = stream_event(stdout, "system", "admission")
            if identity is None:
                self.coordinator.mark_uncertain(record.task_id, record.claim_id, result=stdout,
                    error="Runner restarted without an admission receipt; inspect retained capture before retry")
                self.stop()
                return False
            # Recovery observes the original admission once. An expired/uncertain
            # coordinator lease is not authority to interrupt accepted gateway work.
            original = subprocess.CompletedProcess([], 75, stdout, "Runner restarted during gateway execution")
            result = self._reconcile(saved["profile"], identity, cancel=False, original=original)
            terminal = stream_event(result.stdout or "", "result")
            if terminal is None or terminal.get("settled") is not True:
                self.coordinator.mark_uncertain(record.task_id, record.claim_id, result=stdout, error=result.stderr)
                self.stop()
                return False
            text, telemetry = _parse_stream_json_result(result.stdout or "")
            payload = {"task_id": record.task_id, "claim_id": record.claim_id,
                       "status": "completed" if result.returncode == 0 else "failed",
                       "result": text, "error": _failure_diagnostic(result), "telemetry": telemetry}
            self._save_delivery(payload)
            if not self._send_delivery(payload):
                self.stop()
                return False
        return True

    @staticmethod
    def _execute(argv: list[str], input_text: str, *, cancel=None, output_dir=None, env=None):
        from hermes_cli.fleet_execution import execute_transport
        return execute_transport(argv, input_text, cancel=cancel, output_dir=output_dir, env=env)
