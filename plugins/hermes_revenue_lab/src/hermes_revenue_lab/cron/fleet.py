"""Checksum-bound HRL-14 cron fleet and Hermes command rendering."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from hermes_revenue_lab.routing.policy import PolicyIntegrityError, load_verified_policy
from hermes_revenue_lab.routing.types import RoutingPolicy, TierName

_SHA256 = re.compile(r"[0-9a-f]{64}")
_MODEL_DIGEST = re.compile(r"[0-9a-f]{12,64}")
_SCRIPT_NAME = re.compile(r"hrl14-[a-z0-9_-]+[.]py")
_CRON = re.compile(r"\S+(?:\s+\S+){4}")
ReasoningEffort = Literal["none", "low", "medium", "high"]


class CronFleetIntegrityError(RuntimeError):
    """The fleet cannot be proven to match the governed routing policy."""


@dataclass(frozen=True)
class HermesProviderBinding:
    provider: str
    default_model: str
    endpoint: str
    available_models: tuple[str, ...]


@dataclass(frozen=True)
class CronJob:
    job_id: str
    name: str
    tier: TierName
    schedule: str | None
    trigger: str
    enabled: bool
    disabled_reason: str | None
    no_agent: bool
    script_name: str
    prompt: str
    provider: str | None
    model: str | None
    model_digest: str | None
    reasoning_effort: ReasoningEffort | None
    workload_kind: str
    model_parameters_billions: float | None
    outside_protected_hours: bool
    escalation_flag_required: bool
    deliver: str
    workdir: str


@dataclass(frozen=True)
class CronFleet:
    schema_version: str
    timezone: str
    routing_policy_sha256: str
    expected_provider: str
    expected_default_model: str
    expected_endpoint: str
    jobs: Mapping[str, CronJob]


@dataclass(frozen=True)
class CronPreflightDecision:
    permitted: bool
    reasons: tuple[str, ...]

    def wake_gate(self, *, no_agent: bool) -> dict[str, object]:
        if self.permitted:
            return {
                "context": {"preflight": "permitted"},
                "wakeAgent": not no_agent,
            }
        if no_agent:
            return {
                "context": {"preflight": "blocked", "reason_codes": list(self.reasons)},
                "wakeAgent": True,
            }
        return {"wakeAgent": False}


def _read_mapping(path: Path, name: str) -> tuple[bytes, Mapping[str, object]]:
    try:
        data = path.read_bytes()
        value = json.loads(data)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CronFleetIntegrityError(f"{name} is unavailable or invalid") from exc
    if not isinstance(value, Mapping):
        raise CronFleetIntegrityError(f"{name} must be an object")
    return data, value


def _verify_checksum(manifest_path: Path, checksum_path: Path, payload: bytes) -> None:
    try:
        checksum_text = checksum_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CronFleetIntegrityError("cron fleet checksum is unavailable") from exc
    match = re.fullmatch(r"([0-9a-f]{64})  cron_fleet[.]json\n?", checksum_text)
    if not match or match.group(1) != hashlib.sha256(payload).hexdigest():
        raise CronFleetIntegrityError("cron fleet checksum mismatch")
    if manifest_path.name != "cron_fleet.json":
        # Temporary verification copies are allowed, but the signed payload name is fixed.
        return


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise CronFleetIntegrityError(f"cron job {name} is invalid")
    return value


def _nullable_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, name)


def _job_from_document(job_id: str, value: Mapping[str, object]) -> CronJob:
    expected = {
        "name",
        "tier",
        "schedule",
        "trigger",
        "enabled",
        "disabled_reason",
        "no_agent",
        "script_name",
        "prompt",
        "provider",
        "model",
        "model_digest",
        "reasoning_effort",
        "workload_kind",
        "model_parameters_billions",
        "outside_protected_hours",
        "escalation_flag_required",
        "deliver",
        "workdir",
    }
    if set(value) != expected:
        raise CronFleetIntegrityError(
            f"cron job {job_id} fields do not match the schema"
        )
    tier = value["tier"]
    if tier not in ("no_llm", "fast", "standard", "reasoning", "coding", "escalation"):
        raise CronFleetIntegrityError(f"cron job {job_id} has an invalid tier")
    enabled = value["enabled"]
    no_agent = value["no_agent"]
    outside = value["outside_protected_hours"]
    escalation_required = value["escalation_flag_required"]
    if not all(
        isinstance(item, bool)
        for item in (enabled, no_agent, outside, escalation_required)
    ):
        raise CronFleetIntegrityError(f"cron job {job_id} has invalid Boolean controls")
    schedule = _nullable_string(value["schedule"], "schedule")
    if schedule is not None and not _CRON.fullmatch(schedule):
        raise CronFleetIntegrityError(f"cron job {job_id} has an invalid cron schedule")
    script_name = _required_string(value["script_name"], "script name")
    if not _SCRIPT_NAME.fullmatch(script_name):
        raise CronFleetIntegrityError(f"cron job {job_id} has an invalid script name")
    provider = _nullable_string(value["provider"], "provider")
    model = _nullable_string(value["model"], "model")
    model_digest = _nullable_string(value["model_digest"], "model digest")
    if model_digest is not None and not _MODEL_DIGEST.fullmatch(model_digest):
        raise CronFleetIntegrityError(f"cron job {job_id} has an invalid model digest")
    reasoning = value["reasoning_effort"]
    if reasoning is not None and reasoning not in ("none", "low", "medium", "high"):
        raise CronFleetIntegrityError(f"cron job {job_id} has invalid reasoning effort")
    parameters = value["model_parameters_billions"]
    if parameters is not None and (
        isinstance(parameters, bool)
        or not isinstance(parameters, (int, float))
        or parameters <= 0
    ):
        raise CronFleetIntegrityError(
            f"cron job {job_id} has invalid model size evidence"
        )
    return CronJob(
        job_id=job_id,
        name=_required_string(value["name"], "name"),
        tier=tier,
        schedule=schedule,
        trigger=_required_string(value["trigger"], "trigger"),
        enabled=enabled,
        disabled_reason=_nullable_string(value["disabled_reason"], "disabled reason"),
        no_agent=no_agent,
        script_name=script_name,
        prompt=str(value["prompt"]) if isinstance(value["prompt"], str) else "",
        provider=provider,
        model=model,
        model_digest=model_digest,
        reasoning_effort=reasoning,
        workload_kind=_required_string(value["workload_kind"], "workload kind"),
        model_parameters_billions=float(parameters) if parameters is not None else None,
        outside_protected_hours=outside,
        escalation_flag_required=escalation_required,
        deliver=_required_string(value["deliver"], "delivery target"),
        workdir=_required_string(value["workdir"], "workdir"),
    )


def _validate_job(job: CronJob, policy: RoutingPolicy, fleet: CronFleet) -> None:
    selected = policy.tiers[job.tier]
    if job.tier == "escalation":
        if job.schedule is not None or job.trigger != "manual_or_high_value_gate":
            raise CronFleetIntegrityError(
                "tier4 escalation cannot have a blind schedule"
            )
        if not job.escalation_flag_required:
            raise CronFleetIntegrityError(
                "tier4 escalation requires an escalation flag"
            )
    elif job.schedule is None or job.trigger != "schedule":
        raise CronFleetIntegrityError(f"cron job {job.job_id} requires a schedule")
    if selected.status == "unavailable":
        if job.enabled or job.disabled_reason != "selected_tier_unavailable":
            raise CronFleetIntegrityError(
                f"unavailable tier job {job.job_id} must be disabled"
            )
        if any(
            item is not None for item in (job.model, job.model_digest, job.provider)
        ):
            raise CronFleetIntegrityError(
                f"disabled cron job {job.job_id} cannot substitute a model"
            )
    elif not job.enabled:
        raise CronFleetIntegrityError(
            f"available tier job {job.job_id} must be enabled"
        )
    if job.tier == "no_llm":
        if not job.no_agent or any(
            item is not None
            for item in (
                job.model,
                job.model_digest,
                job.provider,
                job.reasoning_effort,
            )
        ):
            raise CronFleetIntegrityError(
                "no_llm cron jobs must be model-free and no-agent"
            )
    elif selected.status == "available":
        if job.no_agent:
            raise CronFleetIntegrityError(
                f"model cron job {job.job_id} cannot be no-agent"
            )
        if (job.model, job.model_digest) != (selected.model, selected.model_digest):
            raise CronFleetIntegrityError(
                f"cron job {job.job_id} does not match its selected model"
            )
        if job.provider != fleet.expected_provider:
            raise CronFleetIntegrityError(
                f"cron job {job.job_id} has an unexpected provider"
            )
    if job.tier == "coding" and not job.outside_protected_hours:
        raise CronFleetIntegrityError(
            "coding cron job must be marked outside protected hours"
        )


def load_verified_cron_fleet(
    fleet_path: Path,
    fleet_checksum_path: Path,
    policy_path: Path,
    benchmark_path: Path,
    selections_path: Path,
    benchmark_checksums_path: Path,
) -> CronFleet:
    payload, document = _read_mapping(fleet_path, "cron fleet")
    _verify_checksum(fleet_path, fleet_checksum_path, payload)
    expected_fields = {
        "schema_version",
        "timezone",
        "routing_policy_sha256",
        "expected_provider",
        "expected_default_model",
        "expected_endpoint",
        "jobs",
    }
    if (
        set(document) != expected_fields
        or document["schema_version"] != "hrl.cron_fleet.v1"
    ):
        raise CronFleetIntegrityError("cron fleet fields do not match the schema")
    try:
        policy = load_verified_policy(
            policy_path,
            benchmark_path,
            selections_path,
            benchmark_checksums_path,
        )
        policy_payload = policy_path.read_bytes()
    except (PolicyIntegrityError, OSError) as exc:
        raise CronFleetIntegrityError("verified routing policy is unavailable") from exc
    policy_sha = document["routing_policy_sha256"]
    if not isinstance(policy_sha, str) or not _SHA256.fullmatch(policy_sha):
        raise CronFleetIntegrityError("routing policy checksum is invalid")
    if policy_sha != hashlib.sha256(policy_payload).hexdigest():
        raise CronFleetIntegrityError("routing policy checksum mismatch")
    jobs_value = document["jobs"]
    if not isinstance(jobs_value, Mapping) or not jobs_value:
        raise CronFleetIntegrityError("cron fleet jobs are unavailable")
    jobs: dict[str, CronJob] = {}
    for job_id, value in jobs_value.items():
        if not isinstance(job_id, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]{2,63}", job_id
        ):
            raise CronFleetIntegrityError("cron fleet contains an invalid job id")
        if not isinstance(value, Mapping):
            raise CronFleetIntegrityError(f"cron job {job_id} must be an object")
        jobs[job_id] = _job_from_document(job_id, value)
    fleet = CronFleet(
        schema_version="hrl.cron_fleet.v1",
        timezone=_required_string(document["timezone"], "timezone"),
        routing_policy_sha256=policy_sha,
        expected_provider=_required_string(
            document["expected_provider"], "expected provider"
        ),
        expected_default_model=_required_string(
            document["expected_default_model"], "expected default model"
        ),
        expected_endpoint=_required_string(
            document["expected_endpoint"], "expected endpoint"
        ),
        jobs=MappingProxyType(jobs),
    )
    if fleet.timezone != "America/Los_Angeles" or fleet.expected_endpoint != (
        "http://127.0.0.1:11434/v1"
    ):
        raise CronFleetIntegrityError("cron fleet local runtime binding is invalid")
    for job in jobs.values():
        _validate_job(job, policy, fleet)
    return fleet


def build_hermes_create_argv(
    job: CronJob,
    *,
    hermes_executable: Path = Path("/Users/mikedemott/.local/bin/hermes"),
) -> tuple[str, ...]:
    if not job.enabled or job.schedule is None:
        raise ValueError(f"disabled cron job {job.job_id} cannot be installed")
    argv = [
        str(hermes_executable),
        "cron",
        "create",
        job.schedule,
        job.prompt,
        "--name",
        job.name,
        "--deliver",
        job.deliver,
        "--script",
        job.script_name,
        "--workdir",
        job.workdir,
    ]
    if job.no_agent:
        argv.append("--no-agent")
    else:
        assert job.model is not None
        assert job.provider is not None
        assert job.reasoning_effort is not None
        argv.extend(
            (
                "--model",
                job.model,
                "--provider",
                job.provider,
                "--reasoning-effort",
                job.reasoning_effort,
            )
        )
    return tuple(argv)


def preflight_job(
    job: CronJob,
    *,
    live_binding: HermesProviderBinding,
    luna_active: bool,
    resource_permitted: bool = True,
    resource_reasons: Sequence[str] = (),
) -> CronPreflightDecision:
    reasons: list[str] = []
    if not job.enabled:
        reasons.append(job.disabled_reason or "job_disabled")
    if job.model is not None:
        if (
            live_binding.provider != job.provider
            or live_binding.default_model != "hermes-qwen3-fast"
            or live_binding.endpoint != "http://127.0.0.1:11434/v1"
            or job.model not in live_binding.available_models
        ):
            reasons.append("provider_configuration_drift")
        if luna_active:
            reasons.append("luna_active")
    if not resource_permitted:
        reasons.extend(resource_reasons or ("resource_guard_blocked",))
    return CronPreflightDecision(not reasons, tuple(dict.fromkeys(reasons)))
