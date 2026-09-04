"""Immutable, unknown-preserving types for HRL-15 run provenance."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse

from hermes_revenue_lab.ledger.types import parse_timestamp
from hermes_revenue_lab.routing.types import TIER_NAMES, TierName

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_REASON_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MODEL_DIGEST = re.compile(r"[0-9a-f]{12,64}")
_SOURCE_KINDS = {
    "authoritative_public",
    "public_api",
    "public_page",
    "first_party_listing",
    "local_artifact",
}
_ACCESS_STATUSES = {"permitted", "not_applicable", "unknown", "prohibited"}
_RUN_STATUSES = {"completed", "failed", "blocked"}
_DECISIONS = {"none", "continue", "promote", "kill", "block"}


def _identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} is invalid")


def _bounded_text(name: str, value: object, *, maximum: int = 2_000) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{name} is invalid")


def _digest(name: str, value: object) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} digest is invalid")


def _nonnegative_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a nonnegative finite Decimal")


def _elapsed_seconds(started_at: str, ended_at: str) -> Decimal:
    started = parse_timestamp(started_at)
    ended = parse_timestamp(ended_at)
    if ended < started:
        raise ValueError("ended_at cannot precede started_at")
    return Decimal(str((ended - started).total_seconds()))


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    experiment_id: str
    task_name: str
    run_reason: str
    started_at: str
    ended_at: str
    code_commit: str
    routing_policy_sha256: str
    compliance_registry_sha256: str
    approval_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "experiment_id", "task_name"):
            _identifier(name, getattr(self, name))
        _bounded_text("run_reason", self.run_reason)
        _elapsed_seconds(self.started_at, self.ended_at)
        if not isinstance(self.code_commit, str) or not re.fullmatch(
            r"[0-9a-f]{7,64}", self.code_commit
        ):
            raise ValueError("code_commit is invalid")
        _digest("routing policy", self.routing_policy_sha256)
        _digest("compliance registry", self.compliance_registry_sha256)
        if self.approval_id is not None:
            _identifier("approval_id", self.approval_id)

    def canonical_record(self) -> dict[str, object]:
        return {
            "schema_version": "hrl.run_manifest.v1",
            **asdict(self),
            "duration_seconds": _decimal_text(
                _elapsed_seconds(self.started_at, self.ended_at)
            ),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> RunManifest:
        expected = {
            "schema_version",
            "run_id",
            "experiment_id",
            "task_name",
            "run_reason",
            "started_at",
            "ended_at",
            "duration_seconds",
            "code_commit",
            "routing_policy_sha256",
            "compliance_registry_sha256",
            "approval_id",
        }
        if (
            set(document) != expected
            or document.get("schema_version") != "hrl.run_manifest.v1"
        ):
            raise ValueError("run manifest schema is invalid")
        values = dict(document)
        values.pop("schema_version")
        declared_duration = values.pop("duration_seconds")
        result = cls(**values)  # type: ignore[arg-type]
        if declared_duration != _decimal_text(
            _elapsed_seconds(result.started_at, result.ended_at)
        ):
            raise ValueError("run manifest duration does not match timestamps")
        return result


SourceKind = Literal[
    "authoritative_public",
    "public_api",
    "public_page",
    "first_party_listing",
    "local_artifact",
]
AccessStatus = Literal["permitted", "not_applicable", "unknown", "prohibited"]


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    locator: str
    source_kind: SourceKind
    collected_at: str
    content_sha256: str
    permission_basis: str
    license_status: AccessStatus
    terms_status: AccessStatus
    robots_status: AccessStatus

    def __post_init__(self) -> None:
        _identifier("source_id", self.source_id)
        if self.source_kind not in _SOURCE_KINDS:
            raise ValueError("source kind is invalid")
        if self.source_kind == "local_artifact":
            if (
                not isinstance(self.locator, str)
                or not self.locator.startswith("artifact:")
                or ".." in self.locator
                or len(self.locator) > 2_000
            ):
                raise ValueError("local artifact locator is invalid")
        else:
            parsed = urlparse(self.locator)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or len(self.locator) > 2_000
            ):
                raise ValueError("source locator is not a public HTTP reference")
        parse_timestamp(self.collected_at)
        _digest("source content", self.content_sha256)
        _identifier("permission_basis", self.permission_basis)
        for name in ("license_status", "terms_status", "robots_status"):
            if getattr(self, name) not in _ACCESS_STATUSES:
                raise ValueError(f"{name} is invalid")

    @property
    def use_permitted(self) -> bool:
        return all(
            status in {"permitted", "not_applicable"}
            for status in (self.license_status, self.terms_status, self.robots_status)
        )

    def canonical_record(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> SourceRecord:
        expected = set(cls.__dataclass_fields__)
        if set(document) != expected:
            raise ValueError("source record schema is invalid")
        return cls(**dict(document))  # type: ignore[arg-type]


CostStatus = Literal["known", "unknown"]


@dataclass(frozen=True)
class ModelUsage:
    usage_id: str
    requested_tier: TierName
    actual_tier: TierName
    actual_model: str | None
    model_digest: str | None
    provider: str | None
    escalation_reason: str | None
    started_at: str
    ended_at: str
    input_tokens: int | None
    output_tokens: int | None
    cost_status: CostStatus
    estimated_cost_usd: Decimal | None

    def __post_init__(self) -> None:
        _identifier("usage_id", self.usage_id)
        if self.requested_tier not in TIER_NAMES or self.actual_tier not in TIER_NAMES:
            raise ValueError("model usage tier is invalid")
        _elapsed_seconds(self.started_at, self.ended_at)
        if self.actual_tier == "no_llm":
            if (
                self.actual_model is not None
                or self.model_digest is not None
                or self.provider is not None
                or self.input_tokens is not None
                or self.output_tokens is not None
            ):
                raise ValueError("no_llm usage cannot contain model identity or tokens")
        elif (
            not isinstance(self.actual_model, str)
            or not self.actual_model
            or not isinstance(self.model_digest, str)
            or not _MODEL_DIGEST.fullmatch(self.model_digest)
        ):
            raise ValueError("model identity is required for model usage")
        elif not isinstance(self.provider, str) or not _IDENTIFIER.fullmatch(
            self.provider
        ):
            raise ValueError("model provider is required for model usage")
        if self.requested_tier != self.actual_tier:
            if not isinstance(
                self.escalation_reason, str
            ) or not _REASON_CODE.fullmatch(self.escalation_reason):
                raise ValueError("model tier change reason is required")
        elif self.escalation_reason is not None:
            raise ValueError("unchanged model tier cannot claim a tier change reason")
        for name in ("input_tokens", "output_tokens"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} is invalid")
        if self.cost_status == "known":
            _nonnegative_decimal("known cost", self.estimated_cost_usd)
        elif self.cost_status == "unknown":
            if self.estimated_cost_usd is not None:
                raise ValueError("unknown cost cannot contain an amount")
        else:
            raise ValueError("cost_status is invalid")

    def canonical_record(self) -> dict[str, object]:
        value = asdict(self)
        value["duration_seconds"] = _decimal_text(
            _elapsed_seconds(self.started_at, self.ended_at)
        )
        value["estimated_cost_usd"] = (
            None if self.estimated_cost_usd is None else str(self.estimated_cost_usd)
        )
        return value

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> ModelUsage:
        expected = set(cls.__dataclass_fields__) | {"duration_seconds"}
        if set(document) != expected:
            raise ValueError("model usage schema is invalid")
        values = dict(document)
        declared_duration = values.pop("duration_seconds")
        amount = values.get("estimated_cost_usd")
        values["estimated_cost_usd"] = None if amount is None else Decimal(str(amount))
        result = cls(**values)  # type: ignore[arg-type]
        if declared_duration != _decimal_text(
            _elapsed_seconds(result.started_at, result.ended_at)
        ):
            raise ValueError("model usage duration does not match timestamps")
        return result


@dataclass(frozen=True)
class RunVerdict:
    run_id: str
    status: Literal["completed", "failed", "blocked"]
    experiment_decision: Literal["none", "continue", "promote", "kill", "block"]
    reason_codes: tuple[str, ...]
    cost_status: CostStatus
    total_cost_usd: Decimal | None
    revenue_status: Literal["known", "unknown"]
    gross_revenue_usd: Decimal | None
    revenue_ledger_ref: str | None
    output_summary: str

    def __post_init__(self) -> None:
        _identifier("run_id", self.run_id)
        if self.status not in _RUN_STATUSES:
            raise ValueError("run verdict status is invalid")
        if self.experiment_decision not in _DECISIONS:
            raise ValueError("experiment decision is invalid")
        if not self.reason_codes or any(
            not isinstance(code, str) or not _REASON_CODE.fullmatch(code)
            for code in self.reason_codes
        ):
            raise ValueError("run verdict reason codes are invalid")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("run verdict reason codes must be unique")
        self._validate_amount("cost", self.cost_status, self.total_cost_usd)
        self._validate_amount("revenue", self.revenue_status, self.gross_revenue_usd)
        if self.revenue_status == "known":
            if not isinstance(self.revenue_ledger_ref, str) or not re.fullmatch(
                r"ledger:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.revenue_ledger_ref
            ):
                raise ValueError("known revenue requires a ledger reference")
        elif self.revenue_ledger_ref is not None:
            raise ValueError("unknown revenue cannot claim a ledger reference")
        if self.status == "blocked" and self.experiment_decision != "block":
            raise ValueError("blocked runs require a block decision")
        if self.experiment_decision == "block" and self.status != "blocked":
            raise ValueError("block decisions require blocked status")
        if (
            self.experiment_decision in {"continue", "promote"}
            and self.status != "completed"
        ):
            raise ValueError("continue and promote decisions require a completed run")
        _bounded_text("output_summary", self.output_summary, maximum=4_000)

    @staticmethod
    def _validate_amount(name: str, status: str, amount: Decimal | None) -> None:
        if status == "known":
            _nonnegative_decimal(f"known {name}", amount)
        elif status == "unknown":
            if amount is not None:
                raise ValueError(f"unknown {name} cannot contain an amount")
        else:
            raise ValueError(f"{name}_status is invalid")

    def canonical_record(self) -> dict[str, object]:
        value = asdict(self)
        value["reason_codes"] = list(self.reason_codes)
        value["total_cost_usd"] = (
            None if self.total_cost_usd is None else str(self.total_cost_usd)
        )
        value["gross_revenue_usd"] = (
            None if self.gross_revenue_usd is None else str(self.gross_revenue_usd)
        )
        return {"schema_version": "hrl.run_verdict.v1", **value}

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> RunVerdict:
        expected = set(cls.__dataclass_fields__) | {"schema_version"}
        if (
            set(document) != expected
            or document.get("schema_version") != "hrl.run_verdict.v1"
        ):
            raise ValueError("run verdict schema is invalid")
        values = dict(document)
        values.pop("schema_version")
        values["reason_codes"] = tuple(values["reason_codes"])  # type: ignore[arg-type]
        for name in ("total_cost_usd", "gross_revenue_usd"):
            amount = values[name]
            values[name] = None if amount is None else Decimal(str(amount))
        return cls(**values)  # type: ignore[arg-type]
