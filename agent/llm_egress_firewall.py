"""Source-bound policy checks for outbound LLM requests.

The firewall is deliberately transport-agnostic.  Callers hand it the final
logical request and resolved route immediately before invoking a provider.
It returns an immutable allow decision, or raises :class:`EgressBlocked` with
an immutable block decision.
"""

from __future__ import annotations

import base64
import binascii
import fcntl
import ipaddress
import json
import os
import re
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from hmac import compare_digest
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
class LiteralSegment:
    """Application-owned literal text for a typed outbound request."""

    text: str


@dataclass(frozen=True, slots=True)
class SanitizedSegment:
    """Non-source text that must still pass final secret and encoding scans."""

    text: str


@dataclass(frozen=True, slots=True)
class SourceBoundSegment:
    """Opaque reference whose text is loaded only from a verified grant."""

    source_grant_digest: str


@dataclass(frozen=True, slots=True)
class OutboundText:
    """Ordered typed segments that construct one outbound JSON string."""

    segments: tuple[LiteralSegment | SanitizedSegment | SourceBoundSegment, ...]


@dataclass(frozen=True, slots=True)
class TypedOutboundRequest:
    """Remote request recipe; no independent raw string leaf is permitted."""

    payload: Mapping[str, Any]
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
    source_segment_count: int
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


@dataclass(frozen=True, slots=True)
class AuthorizedEgress:
    """Immutable exact bytes that a provider callback is authorized to send."""

    decision: EgressDecision
    payload_bytes: bytes

    @property
    def allowed(self) -> bool:
        return self.decision.allowed

    @property
    def destination_class(self) -> DestinationClass:
        return self.decision.destination_class

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return self.decision.reason_codes

    def verify_payload(self, candidate: bytes) -> bytes:
        """Return the authorized bytes or reject a post-preflight mutation."""

        if not isinstance(candidate, bytes) or not compare_digest(
            sha256(candidate).hexdigest(),
            self.decision.payload_sha256,
        ):
            raise EgressBlocked(
                replace(
                    self.decision,
                    allowed=False,
                    reason_codes=("payload_digest_mismatch",),
                )
            )
        return self.payload_bytes


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:@/-]{0,256}$")
_LOCAL_PROCESS_MODES = frozenset({"local_process", "in_process"})
_BASE64_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9_+/\-])([A-Za-z0-9_+/\-]{4,}={0,2})(?![A-Za-z0-9_+/=\-])"
)
_MAX_BASE64_CANDIDATE_CHARS = 262_144


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
        # ``hostname`` does not validate the port.  Accessing ``port`` is
        # load-bearing: urllib deliberately raises for non-numeric and
        # out-of-range values that must never inherit loopback trust.
        try:
            parsed.port
        except ValueError:
            return DestinationClass.UNKNOWN
        if parsed.netloc.rsplit("@", 1)[-1].endswith(":"):
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


def source_grant_digest(grant: SourceGrant) -> str:
    """Return the opaque identity used by a source-segment manifest."""

    bound_fields = {
        "canonical_path": str(grant.canonical_path),
        "display_path": grant.display_path,
        "line_start": grant.line_start,
        "line_end": grant.line_end,
        "content_sha256": grant.content_sha256,
        "byte_count": grant.byte_count,
        "session_id": grant.session_id,
        "turn_id": grant.turn_id,
        "request_id": grant.request_id,
        "policy_digest": grant.policy_digest,
    }
    encoded = json.dumps(bound_fields, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return sha256(encoded).hexdigest()


def static_literal_sha256(text: str) -> str:
    """Hash one exact UTF-8 static literal for a policy allowlist."""

    if not isinstance(text, str):
        raise TypeError("static literal must be text")
    return sha256(text.encode("utf-8")).hexdigest()


def _canonical_base64_candidate(candidate: str) -> bool:
    """Recognize bounded canonical encodings without flagging ordinary IDs."""

    if not 4 <= len(candidate) <= _MAX_BASE64_CANDIDATE_CHARS:
        return False
    unpadded = candidate.rstrip("=")
    if "=" in unpadded or len(unpadded) % 4 == 1:
        return False
    padded = unpadded + "=" * (-len(unpadded) % 4)
    encoded = padded.encode("ascii")
    for altchars in (None, b"-_"):
        try:
            decoded = base64.b64decode(encoded, altchars=altchars, validate=True)
        except (binascii.Error, ValueError):
            continue
        canonical_padded = (
            base64.b64encode(decoded)
            if altchars is None
            else base64.urlsafe_b64encode(decoded)
        ).decode("ascii")
        if candidate in {canonical_padded, canonical_padded.rstrip("=")} and decoded:
            return True
    return False


def _contains_canonical_base64(value: Any, *, seen: set[int] | None = None) -> bool:
    if isinstance(value, str):
        return any(_canonical_base64_candidate(match.group(1)) for match in _BASE64_CANDIDATE.finditer(value))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return True
    if seen is None:
        seen = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return True
        seen.add(identity)
        # Typed request keys are application-owned structure, not payload
        # segments. Only values can carry caller-controlled concealment.
        return any(_contains_canonical_base64(item, seen=seen) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        identity = id(value)
        if identity in seen:
            return True
        seen.add(identity)
        return any(_contains_canonical_base64(item, seen=seen) for item in value)
    return False


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
        static_literal_hashes_by_policy: Mapping[str, Sequence[str]] | None = None,
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
        self._static_literal_hashes_by_policy = {
            str(policy_digest): frozenset(
                digest
                for digest in digests
                if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
            )
            for policy_digest, digests in (static_literal_hashes_by_policy or {}).items()
        }

    def preflight(
        self,
        request: Mapping[str, Any] | TypedOutboundRequest,
        route: Any,
        *,
        grants: Sequence[SourceGrant] = (),
    ) -> EgressDecision:
        """Preserve the content-free Task 1 decision interface."""

        return self.authorize(request, route, grants=grants).decision

    def authorize(
        self,
        request: Mapping[str, Any] | TypedOutboundRequest,
        route: Any,
        *,
        grants: Sequence[SourceGrant] = (),
    ) -> AuthorizedEgress:
        """Construct and authorize immutable provider bytes or fail closed.

        Remote and unknown destinations accept only ``TypedOutboundRequest``.
        Source text is loaded from verified grants while constructing the
        logical request, so no independent raw source-bearing payload exists
        for a caller to send after preflight.
        """

        provider = str(_route_value(route, "provider", ""))
        model = str(_route_value(route, "model", ""))
        destination = classify_destination(
            provider,
            _route_value(route, "base_url"),
            _route_value(route, "api_mode"),
        )
        typed_request = request if isinstance(request, TypedOutboundRequest) else None
        if typed_request is not None:
            session_id = typed_request.session_id
            turn_id = typed_request.turn_id
            request_id = typed_request.request_id
            policy_digest = typed_request.policy_digest
        else:
            session_id = _request_identity(request, "session_id")
            turn_id = _request_identity(request, "turn_id")
            request_id = _request_identity(request, "request_id")
            policy_digest = _request_identity(request, "policy_digest")

        reasons: list[str] = []
        valid_grants: list[SourceGrant] = []
        grant_contents: dict[str, tuple[SourceGrant, bytes]] = {}
        source_segment_count = 0

        if destination == DestinationClass.UNKNOWN:
            reasons.append("unknown_destination")
        if destination in {DestinationClass.REMOTE, DestinationClass.UNKNOWN}:
            if typed_request is None:
                reasons.append("typed_request_required")
            grant_reasons, valid_grants, grant_contents = self._validate_grants(
                grants,
                session_id=session_id,
                turn_id=turn_id,
                request_id=request_id,
                policy_digest=policy_digest,
            )
            reasons.extend(grant_reasons)

        if typed_request is not None:
            logical_request, construction_reasons, source_segment_count, scan_values = (
                self._construct_typed_request(typed_request, grant_contents)
            )
            reasons.extend(construction_reasons)
        else:
            logical_request = request
            scan_values = request

        try:
            serialized = json.dumps(
                logical_request,
                ensure_ascii=False,
                allow_nan=False,
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
                source_grant_count=len(valid_grants),
                source_segment_count=source_segment_count,
                session_id=session_id,
                turn_id=turn_id,
                request_id=request_id,
                policy_digest=policy_digest,
                reason_codes=("serialization_failed",),
            )
            self._block(decision, valid_grants)

        serialized_bytes = len(serialized)
        estimated_tokens = (
            serialized_bytes + self._conservative_chars_per_token - 1
        ) // self._conservative_chars_per_token
        if serialized_bytes > self._max_serialized_bytes:
            reasons.append("serialized_bytes_exceeded")
        if estimated_tokens > self._max_conservative_tokens:
            reasons.append("token_cap_exceeded")

        if destination in {DestinationClass.REMOTE, DestinationClass.UNKNOWN}:
            try:
                if _contains_secret(scan_values):
                    reasons.append("secret_detected")
            except Exception:
                reasons.append("redaction_failed")
            try:
                if _contains_canonical_base64(scan_values):
                    reasons.append("base64_payload")
            except Exception:
                reasons.append("base64_scan_failed")

        decision = EgressDecision(
            allowed=not reasons,
            destination_class=destination,
            provider=provider,
            model=model,
            payload_sha256=sha256(serialized).hexdigest(),
            serialized_bytes=serialized_bytes,
            estimated_tokens=estimated_tokens,
            source_grant_count=len(valid_grants),
            source_segment_count=source_segment_count,
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
        return AuthorizedEgress(decision=decision, payload_bytes=serialized)

    def _validate_grants(
        self,
        grants: Sequence[SourceGrant],
        *,
        session_id: str,
        turn_id: str,
        request_id: str,
        policy_digest: str,
    ) -> tuple[list[str], list[SourceGrant], dict[str, tuple[SourceGrant, bytes]]]:
        reasons: list[str] = []
        valid: list[SourceGrant] = []
        contents: dict[str, tuple[SourceGrant, bytes]] = {}
        if not grants:
            return ["untrusted_provenance"], valid, contents

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
            contents[source_grant_digest(grant)] = (grant, content)

        if not valid and "untrusted_provenance" not in reasons:
            reasons.append("untrusted_provenance")
        return reasons, valid, contents

    def _construct_typed_request(
        self,
        request: TypedOutboundRequest,
        grant_contents: Mapping[str, tuple[SourceGrant, bytes]],
    ) -> tuple[Mapping[str, Any], list[str], int, list[str]]:
        """Build a plain JSON request exclusively from typed segment nodes."""

        reasons: list[str] = []
        referenced_grants: set[str] = set()
        source_segment_count = 0
        scan_values: list[str] = []
        allowed_static_hashes = self._static_literal_hashes_by_policy.get(
            request.policy_digest,
            frozenset(),
        )

        def require_static_literal(text: str) -> None:
            if static_literal_sha256(text) not in allowed_static_hashes:
                reasons.append("static_literal_not_allowed")
                scan_values.append(text)

        def render_text_segment(
            segment: LiteralSegment | SanitizedSegment | SourceBoundSegment,
        ) -> str:
            nonlocal source_segment_count
            if isinstance(segment, LiteralSegment):
                if not isinstance(segment.text, str):
                    reasons.append("invalid_literal_segment")
                    return ""
                require_static_literal(segment.text)
                encoded_literal = segment.text.encode("utf-8")
                if any(content and content in encoded_literal for _, content in grant_contents.values()):
                    reasons.append("source_bytes_in_literal")
                return segment.text
            if isinstance(segment, SanitizedSegment):
                reasons.append("sanitized_segment_forbidden")
                if isinstance(segment.text, str):
                    scan_values.append(segment.text)
                    return segment.text
                reasons.append("invalid_literal_segment")
                return ""
            if isinstance(segment, SourceBoundSegment):
                grant_and_content = grant_contents.get(segment.source_grant_digest)
                if grant_and_content is None:
                    reasons.append("source_segment_grant_mismatch")
                    return ""
                try:
                    text = grant_and_content[1].decode("utf-8")
                except UnicodeDecodeError:
                    reasons.append("source_segment_not_text")
                    return ""
                referenced_grants.add(segment.source_grant_digest)
                source_segment_count += 1
                scan_values.append(text)
                return text
            reasons.append("invalid_source_segment")
            return ""

        def render(value: Any) -> Any:
            if isinstance(value, (LiteralSegment, SanitizedSegment, SourceBoundSegment)):
                return render_text_segment(value)
            if isinstance(value, OutboundText):
                return "".join(render_text_segment(segment) for segment in value.segments)
            if isinstance(value, Mapping):
                rendered: dict[str, Any] = {}
                for key, item in value.items():
                    if not isinstance(key, str):
                        reasons.append("invalid_request_key")
                        continue
                    require_static_literal(key)
                    rendered[key] = render(item)
                return rendered
            if isinstance(value, (list, tuple)):
                return [render(item) for item in value]
            if value is None or isinstance(value, (bool, int, float)):
                require_static_literal(
                    json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
                )
                return value
            # In particular, raw strings and bytes are not remote request
            # material. Every outbound string must have a typed owner.
            reasons.append("untyped_request_value")
            return None

        rendered_payload = render(request.payload)
        if not isinstance(rendered_payload, Mapping):
            reasons.append("invalid_typed_request_root")
            rendered_payload = {}
        else:
            bound_identities = {
                "session_id": request.session_id,
                "turn_id": request.turn_id,
                "request_id": request.request_id,
                "policy_digest": request.policy_digest,
            }
            if any(
                field in rendered_payload and rendered_payload[field] != expected
                for field, expected in bound_identities.items()
            ):
                reasons.append("request_identity_mismatch")
        if set(grant_contents) - referenced_grants:
            reasons.append("source_grant_unbound")
        return rendered_payload, reasons, source_segment_count, scan_values

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
    "AuthorizedEgress",
    "DestinationClass",
    "EgressBlocked",
    "EgressDecision",
    "LLMEgressFirewall",
    "LiteralSegment",
    "OutboundText",
    "SanitizedSegment",
    "SourceBoundSegment",
    "SourceGrant",
    "TypedOutboundRequest",
    "classify_destination",
    "source_grant_digest",
    "static_literal_sha256",
]
