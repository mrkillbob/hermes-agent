"""Immutable bounded source evidence for HRL-7 scouts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from hermes_revenue_lab.ledger.types import parse_timestamp


ScoutKind = Literal[
    "business_problem",
    "data_opportunity",
    "alert_opportunity",
    "digital_product",
]
SourceClass = Literal["authoritative_public", "public_api", "public_page", "first_party_listing"]
_SCOUT_KINDS = {"business_problem", "data_opportunity", "alert_opportunity", "digital_product"}
_SOURCE_CLASSES = {"authoritative_public", "public_api", "public_page", "first_party_listing"}
_PERMISSIONS = {"publicly_accessible", "public_api_terms", "public_record", "first_party_public"}
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} is invalid")


@dataclass(frozen=True)
class ScoutEvidence:
    evidence_id: str
    source_url: str
    source_class: SourceClass
    permission_basis: str
    collected_at: str
    content_sha256: str
    fact_code: str
    fact_value: str

    def __post_init__(self) -> None:
        _identifier("evidence_id", self.evidence_id)
        parsed = urlparse(self.source_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or len(self.source_url) > 2_000
        ):
            raise ValueError("scout source URL is not a public HTTP reference")
        if self.source_class not in _SOURCE_CLASSES:
            raise ValueError("scout source class is invalid")
        if self.permission_basis not in _PERMISSIONS:
            raise ValueError("scout permission basis is invalid")
        parse_timestamp(self.collected_at)
        if not _SHA256.fullmatch(self.content_sha256):
            raise ValueError("scout content digest is invalid")
        _identifier("fact_code", self.fact_code)
        if not isinstance(self.fact_value, str) or not 1 <= len(self.fact_value) <= 2_000:
            raise ValueError("scout fact value is invalid")


@dataclass(frozen=True)
class ScoutCandidate:
    candidate_id: str
    scout_kind: ScoutKind
    subject: str
    evidence: tuple[ScoutEvidence, ...]

    def __post_init__(self) -> None:
        _identifier("candidate_id", self.candidate_id)
        if self.scout_kind not in _SCOUT_KINDS:
            raise ValueError("scout kind is invalid")
        if not isinstance(self.subject, str) or not 1 <= len(self.subject) <= 500:
            raise ValueError("scout subject is invalid")
        if not 1 <= len(self.evidence) <= 64:
            raise ValueError("candidate evidence bound is one to 64 facts")
        identities = tuple(item.evidence_id for item in self.evidence)
        if len(identities) != len(set(identities)):
            raise ValueError("candidate evidence IDs must be unique")


@dataclass(frozen=True)
class ScoutVerdict:
    candidate_id: str
    eligible: bool
    reasons: tuple[str, ...]
