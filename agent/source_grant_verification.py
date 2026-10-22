"""Final canonical verification for ordinary slices and dedicated bounded guidance proofs."""
from hashlib import sha256
from pathlib import Path
import re
import logging
from typing import Sequence
from agent.file_safety import get_read_block_error
from agent.llm_egress_firewall import SourceGrant, source_grant_digest
from agent.source_context_preview import ContextPreviewGrant, verify_preview


def validate_source_grants(
    grants: Sequence[SourceGrant],
    *,
    session_id: str,
    turn_id: str,
    request_id: str,
    policy_digest: str,
) -> tuple[list[str], list[SourceGrant], dict[str, tuple[SourceGrant, bytes]], list[str]]:
    reasons: list[str] = []
    valid: list[SourceGrant] = []
    contents: dict[str, tuple[SourceGrant, bytes]] = {}
    snapshot_scans = []
    if not grants:
        return ["untrusted_provenance"], valid, contents, snapshot_scans

    for candidate in grants:
        if isinstance(candidate, ContextPreviewGrant):
            if any(getattr(candidate, name) != value for name, value in dict(
                    session_id=session_id, turn_id=turn_id, request_id=request_id, policy_digest=policy_digest).items()):
                reasons.append('grant_binding_mismatch')
                continue
            try:
                preview, full_text = verify_preview(candidate)
            except (OSError, ValueError, RuntimeError, TypeError):
                reasons.append('invalid_preview_proof')
                continue
            valid.append(candidate)
            contents[source_grant_digest(candidate)] = (candidate, preview)
            snapshot_scans.append(full_text)
            continue
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
            logging.getLogger(__name__).debug('Source policy unavailable', exc_info=True)
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
    return reasons, valid, contents, snapshot_scans



def grant_receipt_fields(grant):
    if isinstance(grant, ContextPreviewGrant):
        return dict(proof_kind='context_hint_bounded_preview_v1', full_source_sha256=grant.full_source_sha256,
                    full_source_bytes=grant.full_source_bytes, preview_sha256=grant.preview_sha256,
                    preview_bytes=grant.preview_bytes)
    return dict(line_start=grant.line_start, line_end=grant.line_end,
                content_sha256=grant.content_sha256, byte_count=grant.byte_count)
