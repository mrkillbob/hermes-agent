"""Bounded deterministic guidance proofs; full snapshots are verified, never granted."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import os
import re
from pathlib import Path

PREVIEW_PROOF_KIND = 'context_hint_bounded_preview_v1:'


@dataclass(frozen=True, slots=True)
class ContextPreviewGrant:
    canonical_path: Path
    display_path: str
    working_root: Path
    relative_alias: str
    source_device: int
    source_inode: int
    full_source_sha256: str
    full_source_bytes: int
    preview_sha256: str
    preview_bytes: int
    session_id: str
    turn_id: str
    request_id: str
    policy_digest: str


def preview_digest_fields(grant):
    fields = asdict(grant)
    for name in ('canonical_path', 'working_root'):
        fields[name] = str(fields[name])
    return {'proof_kind':PREVIEW_PROOF_KIND, **fields}


def _snapshot_fd(path):
    from agent.source_provenance import (
        SourceProvenanceError, _contains_symlink_component, _open_verified_source, get_read_block_error,
    )
    if not path.is_absolute() or path.resolve(strict=True) != path or _contains_symlink_component(path):
        raise SourceProvenanceError('source_path_not_canonical')
    descriptor = _open_verified_source(path)
    try:
        before = os.fstat(descriptor)
        if get_read_block_error(str(path)) is not None:
            raise SourceProvenanceError('sensitive_path')
        chunks = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
        raw = b''.join(chunks)
        after, named = os.fstat(descriptor), path.stat()
        def identity(value):
            return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if identity(before) != identity(after) or identity(after) != identity(named):
            raise SourceProvenanceError('source_changed')
        if path.resolve(strict=True) != path or get_read_block_error(str(path)) is not None:
            raise SourceProvenanceError('source_changed')
        return raw, (after.st_dev, after.st_ino)
    finally:
        os.close(descriptor)


def snapshot(path):
    from agent.source_context_presentation import read_context_bytes
    from agent.source_provenance import SourceProvenanceError
    from agent.redact import redact_sensitive_text
    try:
        stamp = None
        def read(target):
            nonlocal stamp
            raw, stamp = _snapshot_fd(target)
            return raw
        raw = read_context_bytes(path, reader=read)
        if raw is None:
            raise SourceProvenanceError('source_read_timeout')
        text = raw.decode('utf-8')
        if redact_sensitive_text(text, force=True, file_read=True, redact_url_credentials=True) != text:
            raise SourceProvenanceError('redaction_changed_content')
        return raw, text, stamp
    except (OSError, ValueError, RuntimeError) as exc:
        raise SourceProvenanceError('preview_snapshot_unavailable') from exc


def rendered_preview(raw, root, relative, path):
    from agent.source_context_presentation import context_display_text
    from agent.source_context_rendering import hint_relative_path, render_hint_preview
    from agent.source_provenance import MAX_SOURCE_SLICE_BYTES, SourceProvenanceError
    from agent.subdirectory_hints import _resolved_hint_target
    alias = hint_relative_path(relative)
    if (alias is None or root.resolve(strict=True) != root
            or _resolved_hint_target(root.joinpath(*alias.parts), root) != path):
        raise SourceProvenanceError('preview_alias_mismatch')
    result = render_hint_preview(context_display_text(raw.decode('utf-8')), relative)
    if result is None or len(result.encode('utf-8')) > MAX_SOURCE_SLICE_BYTES:
        raise SourceProvenanceError('preview_capacity')
    return result


def requires_preview_proof(raw):
    from agent.source_provenance import MAX_SOURCE_SLICE_BYTES, MAX_SOURCE_SLICE_LINES
    lines = raw.count(b'\n') + int(not raw.endswith(b'\n'))
    return len(raw) > MAX_SOURCE_SLICE_BYTES or lines > MAX_SOURCE_SLICE_LINES


def issue_preview(registry, *, path, raw_bytes, working_root, relative_path, identity, reuse=False, expected_snapshot=None):
    from agent.llm_egress_firewall import (
        _contains_secret, _contains_private_absolute_path, _contains_canonical_base64,
        _contains_exact_secret, _source_text_for_secret_scan, _source_text_for_base64_scan,
    )
    from agent.llm_egress_runtime import _exact_provider_secret_values
    from agent.source_provenance import SourceProvenanceError, _safe_display_path
    if not all(isinstance(value, str) and value for value in identity.values()):
        raise SourceProvenanceError('missing_identity')
    raw, text, stamp = snapshot(path)
    actual_snapshot = (sha256(raw).hexdigest(), len(raw), *stamp)
    if expected_snapshot is not None and actual_snapshot != expected_snapshot:
        raise SourceProvenanceError('source_changed')
    if not requires_preview_proof(raw):
        raise SourceProvenanceError('ordinary_slice_required')
    if raw != raw_bytes:
        raise SourceProvenanceError('content_mismatch')
    secret_text = _source_text_for_secret_scan(text)
    if (_contains_secret(secret_text) or _contains_private_absolute_path(secret_text)
            or _contains_exact_secret(secret_text, _exact_provider_secret_values())
            or _contains_canonical_base64(_source_text_for_base64_scan(text, allow_fixed_source_literals=True))):
        raise SourceProvenanceError('unsafe_full_guidance')
    preview = rendered_preview(raw, working_root, relative_path, path).encode('utf-8')
    grant = ContextPreviewGrant(path, _safe_display_path(path), working_root, relative_path,
                               *stamp, sha256(raw).hexdigest(), len(raw), sha256(preview).hexdigest(), len(preview), **identity)
    return registry._store_verified_grant(grant, reuse_existing=reuse)


def verify_preview(grant):
    from agent.source_provenance import MAX_SOURCE_SLICE_BYTES, SourceProvenanceError
    if (not isinstance(grant, ContextPreviewGrant)
            or not isinstance(grant.preview_bytes, int) or not 0 < grant.preview_bytes <= MAX_SOURCE_SLICE_BYTES
            or not isinstance(grant.full_source_bytes, int) or grant.full_source_bytes < 0
            or not isinstance(grant.full_source_sha256, str) or re.fullmatch(r'[0-9a-f]{64}', grant.full_source_sha256) is None
            or not isinstance(grant.preview_sha256, str) or re.fullmatch(r'[0-9a-f]{64}', grant.preview_sha256) is None
            or Path(grant.display_path).is_absolute() or '..' in Path(grant.display_path).parts):
        raise SourceProvenanceError('invalid_preview_proof')
    raw, text, stamp = snapshot(grant.canonical_path)
    if not requires_preview_proof(raw):
        raise SourceProvenanceError('ordinary_slice_required')
    if (len(raw) != grant.full_source_bytes or sha256(raw).hexdigest() != grant.full_source_sha256
            or stamp != (grant.source_device, grant.source_inode)):
        raise SourceProvenanceError('source_hash_mismatch')
    preview = rendered_preview(raw, grant.working_root, grant.relative_alias, grant.canonical_path).encode('utf-8')
    if len(preview) != grant.preview_bytes or sha256(preview).hexdigest() != grant.preview_sha256:
        raise SourceProvenanceError('preview_hash_mismatch')
    return preview, text
