"""Source-bound policy checks for outbound LLM requests.

The firewall is deliberately transport-agnostic.  Callers hand it the final
logical request and resolved route immediately before invoking a provider.
It returns an immutable allow decision, or raises :class:`EgressBlocked` with
an immutable block decision.
"""

from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import re
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from agent.file_safety import get_read_block_error
from agent.redact import redact_sensitive_text


class DestinationClass(StrEnum):
    """Trust class for an LLM destination."""

    LOCAL_PROCESS = "local_process"
    LOOPBACK = "loopback"
    REMOTE = "remote"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SourceGrant:
    """Immutable authorization for one exact, already-read file slice."""

    canonical_path: Path
    display_path: str
    line_start: int
    line_end: int
    content_sha256: str
    byte_count: int
    session_id: str
    turn_id: str
    request_id: str
    policy_digest: str


@dataclass(frozen=True, slots=True)
class EgressDecision:
    """Content-free result of one egress preflight."""

    allowed: bool
    destination_class: DestinationClass
    provider: str
    model: str
    payload_sha256: str
    serialized_bytes: int
    estimated_tokens: int
    source_grant_count: int
    session_id: str
    turn_id: str
    request_id: str
    policy_digest: str
    reason_codes: tuple[str, ...] = ()


class EgressBlocked(RuntimeError):
    """Raised when the final request is not authorized for its destination."""

    def __init__(self, decision: EgressDecision):
        self.decision = decision
        reasons = ",".join(decision.reason_codes) or "policy_denied"
        super().__init__(f"LLM egress blocked: {reasons}")


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:@/-]{0,256}$")
_LOCAL_PROCESS_MODES = frozenset({"local_process", "in_process"})


def classify_destination(
    provider: str,
    base_url: str | None,
    api_mode: str | None,
) -> DestinationClass:
    """Classify without DNS, provider-name, or private-network trust.

    Only an explicit in-process mode or a numeric loopback literal is local.
    LAN, Tailscale, container DNS, ``localhost``, and other hostnames retain
    remote policy.  Missing or malformed endpoint data is unknown.
    """

    del provider  # Provider labels are not a security boundary.
    mode = str(api_mode or "").strip().lower()
    if mode in _LOCAL_PROCESS_MODES:
        return DestinationClass.LOCAL_PROCESS
    if not isinstance(base_url, str) or not base_url.strip():
        return DestinationClass.UNKNOWN
    try:
        parsed = urlsplit(base_url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return DestinationClass.UNKNOWN
        host = parsed.hostname
        if not host:
            return DestinationClass.UNKNOWN
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return DestinationClass.REMOTE
        return DestinationClass.LOOPBACK if address.is_loopback else DestinationClass.REMOTE
    except (TypeError, ValueError):
        return DestinationClass.UNKNOWN


def _route_value(route: Any, name: str, default: Any = None) -> Any:
    if isinstance(route, Mapping):
        return route.get(name, default)
    return getattr(route, name, default)


def _request_identity(request: Mapping[str, Any], name: str) -> str:
    value = request.get(name, "")
    return value if isinstance(value, str) else str(value)


def _receipt_identifier(value: str) -> str:
    """Keep ordinary correlation IDs while hashing unsafe or sensitive labels."""

    try:
        safe = (
            _SAFE_ID.fullmatch(value) is not None
            and not value.startswith(("/", "~"))
            and redact_sensitive_text(
                value,
                force=True,
                redact_url_credentials=True,
            )
            == value
        )
    except Exception:
        safe = False
    return value if safe else f"sha256:{sha256(value.encode('utf-8')).hexdigest()}"


def _contains_secret(value: Any, *, seen: set[int] | None = None) -> bool:
    """Apply forced redaction semantics independently to every request string."""

    if isinstance(value, str):
        return redact_sensitive_text(
            value,
            force=True,
            redact_url_credentials=True,
        ) != value
    if isinstance(value, (bytes, bytearray, memoryview)):
        # Binary request material is not safely inspectable as text.
        return True
    if seen is None:
        seen = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return True
        seen.add(identity)
        return any(
            _contains_secret(key, seen=seen) or _contains_secret(item, seen=seen)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        identity = id(value)
        if identity in seen:
            return True
        seen.add(identity)
        return any(_contains_secret(item, seen=seen) for item in value)
    return False


class LLMEgressFirewall:
    """Validate a final LLM request and record a content-free receipt."""

    def __init__(
        self,
        state_dir: Path | str,
        *,
        max_serialized_bytes: int = 262_144,
        max_conservative_tokens: int = 87_382,
        conservative_chars_per_token: int = 3,
    ) -> None:
        if max_serialized_bytes <= 0:
            raise ValueError("max_serialized_bytes must be positive")
        if max_conservative_tokens <= 0:
            raise ValueError("max_conservative_tokens must be positive")
        if conservative_chars_per_token <= 0:
            raise ValueError("conservative_chars_per_token must be positive")
        self._state_dir = Path(state_dir)
        self._receipt_path = self._state_dir / "llm-egress-receipts.jsonl"
        self._max_serialized_bytes = max_serialized_bytes
        self._max_conservative_tokens = max_conservative_tokens
        self._conservative_chars_per_token = conservative_chars_per_token

    def preflight(
        self,
        request: Mapping[str, Any],
        route: Any,
        *,
        grants: Sequence[SourceGrant] = (),
    ) -> EgressDecision:
        """Authorize one final request or raise a typed, fail-closed result."""

        provider = str(_route_value(route, "provider", ""))
        model = str(_route_value(route, "model", ""))
        destination = classify_destination(
            provider,
            _route_value(route, "base_url"),
            _route_value(route, "api_mode"),
        )
        session_id = _request_identity(request, "session_id")
        turn_id = _request_identity(request, "turn_id")
        request_id = _request_identity(request, "request_id")
        policy_digest = _request_identity(request, "policy_digest")

        try:
            serialized = json.dumps(
                request,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError):
            decision = EgressDecision(
                allowed=False,
                destination_class=destination,
                provider=provider,
                model=model,
                payload_sha256="",
                serialized_bytes=0,
                estimated_tokens=0,
                source_grant_count=0,
                session_id=session_id,
                turn_id=turn_id,
                request_id=request_id,
                policy_digest=policy_digest,
                reason_codes=("serialization_failed",),
            )
            self._block(decision)

        serialized_bytes = len(serialized)
        estimated_tokens = (
            serialized_bytes + self._conservative_chars_per_token - 1
        ) // self._conservative_chars_per_token
        reasons: list[str] = []
        valid_grants: list[SourceGrant] = []

        if destination == DestinationClass.UNKNOWN:
            reasons.append("unknown_destination")
        if serialized_bytes > self._max_serialized_bytes:
            reasons.append("serialized_bytes_exceeded")
        if estimated_tokens > self._max_conservative_tokens:
            reasons.append("token_cap_exceeded")

        if destination in {DestinationClass.REMOTE, DestinationClass.UNKNOWN}:
            try:
                if _contains_secret(request):
                    reasons.append("secret_detected")
            except Exception:
                reasons.append("redaction_failed")

        if destination == DestinationClass.REMOTE:
            grant_reasons, valid_grants = self._validate_grants(
                grants,
                session_id=session_id,
                turn_id=turn_id,
                request_id=request_id,
                policy_digest=policy_digest,
            )
            reasons.extend(grant_reasons)

        decision = EgressDecision(
            allowed=not reasons,
            destination_class=destination,
            provider=provider,
            model=model,
            payload_sha256=sha256(serialized).hexdigest(),
            serialized_bytes=serialized_bytes,
            estimated_tokens=estimated_tokens,
            source_grant_count=len(valid_grants),
            session_id=session_id,
            turn_id=turn_id,
            request_id=request_id,
            policy_digest=policy_digest,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )
        if not decision.allowed:
            self._block(decision, valid_grants)

        try:
            self._append_receipt(decision, valid_grants)
        except OSError:
            raise EgressBlocked(
                replace(decision, allowed=False, reason_codes=("receipt_unavailable",))
            ) from None
        return decision

    def _validate_grants(
        self,
        grants: Sequence[SourceGrant],
        *,
        session_id: str,
        turn_id: str,
        request_id: str,
        policy_digest: str,
    ) -> tuple[list[str], list[SourceGrant]]:
        reasons: list[str] = []
        valid: list[SourceGrant] = []
        if not grants:
            return ["untrusted_provenance"], valid

        for candidate in grants:
            if not isinstance(candidate, SourceGrant):
                reasons.append("untrusted_provenance")
                continue
            grant = candidate
            if (
                grant.session_id != session_id
                or grant.turn_id != turn_id
                or grant.request_id != request_id
                or grant.policy_digest != policy_digest
            ):
                reasons.append("grant_binding_mismatch")
                continue
            if (
                grant.line_start < 1
                or grant.line_end < grant.line_start
                or grant.byte_count < 0
                or not re.fullmatch(r"[0-9a-f]{64}", grant.content_sha256)
            ):
                reasons.append("invalid_source_grant")
                continue
            display = Path(grant.display_path)
            if display.is_absolute() or ".." in display.parts:
                reasons.append("invalid_display_path")
                continue
            try:
                canonical = Path(grant.canonical_path)
                resolved = canonical.resolve(strict=True)
            except (OSError, RuntimeError, ValueError, TypeError):
                reasons.append("source_unavailable")
                continue
            if not canonical.is_absolute() or canonical != resolved:
                reasons.append("source_path_not_canonical")
                continue
            try:
                blocked = get_read_block_error(str(resolved))
            except Exception:
                reasons.append("source_policy_unavailable")
                continue
            if blocked is not None:
                reasons.append("sensitive_path")
                continue
            try:
                lines = resolved.read_bytes().splitlines(keepends=True)
            except OSError:
                reasons.append("source_unavailable")
                continue
            if grant.line_end > len(lines):
                reasons.append("source_range_mismatch")
                continue
            content = b"".join(lines[grant.line_start - 1 : grant.line_end])
            if len(content) != grant.byte_count or sha256(content).hexdigest() != grant.content_sha256:
                reasons.append("source_hash_mismatch")
                continue
            valid.append(grant)

        if not valid and "untrusted_provenance" not in reasons:
            reasons.append("untrusted_provenance")
        return reasons, valid

    def _block(
        self,
        decision: EgressDecision,
        grants: Sequence[SourceGrant] = (),
    ) -> None:
        try:
            self._append_receipt(decision, grants)
        except OSError:
            decision = replace(
                decision,
                reason_codes=tuple(dict.fromkeys((*decision.reason_codes, "receipt_unavailable"))),
            )
        raise EgressBlocked(decision)

    def _append_receipt(
        self,
        decision: EgressDecision,
        grants: Sequence[SourceGrant],
    ) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        receipt = asdict(decision)
        receipt["destination_class"] = decision.destination_class.value
        receipt["decision"] = "allow" if decision.allowed else "block"
        for field in (
            "provider",
            "model",
            "session_id",
            "turn_id",
            "request_id",
            "policy_digest",
        ):
            receipt[field] = _receipt_identifier(receipt[field])
        receipt["source_grants"] = [
            {
                "line_start": grant.line_start,
                "line_end": grant.line_end,
                "content_sha256": grant.content_sha256,
                "byte_count": grant.byte_count,
            }
            for grant in grants
        ]
        encoded = (
            json.dumps(receipt, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode("utf-8")
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self._receipt_path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            if os.write(fd, encoded) != len(encoded):
                raise OSError("short receipt write")
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


__all__ = [
    "DestinationClass",
    "EgressBlocked",
    "EgressDecision",
    "LLMEgressFirewall",
    "SourceGrant",
    "classify_destination",
]
