"""Exact source proofs for a read result followed by discovered repository guidance."""
from __future__ import annotations

from hashlib import sha256
import json
import logging
import queue
from pathlib import Path, PurePosixPath
import re

from agent.llm_egress_firewall import (
    SanitizedSegment, OutboundText, SourcePresentationSegment, UntrustedProvenanceSegment,
    ValidatedToolSyntaxSegment, source_grant_digest,
)

READ_WITH_CONTEXT = 'read_file_with_context_v1'
CONTEXT_TEXT = 'context_file_normalized_v1'
_HEADER = re.compile(r'\n\n\[Subdirectory context discovered: ([^\]\n]+)\]\n')


def presentation_kind(content):
    """An appended context envelope is not a JSON read-file presentation."""
    if isinstance(content, str):
        try:
            _, end = json.JSONDecoder().raw_decode(content)
            if _HEADER.match(content, end):
                return READ_WITH_CONTEXT
        except (ValueError, TypeError):
            pass
    return 'read_file_json_v1'


def issue_context_read(path: Path, raw_bytes: bytes, presented_text: str, *, working_root=None,
                       relative_path=None) -> None:
    """Use the existing verified-read policy, never the filename as authorization."""
    from agent.source_provenance import active_source_provenance, SourceProvenanceError
    from agent.source_context_rendering import render_hint_preview
    from agent.prompt_builder import _scan_context_content
    from agent.subdirectory_hints import _resolved_hint_target

    context = active_source_provenance()
    if context is None or not raw_bytes:
        return
    normalized = context_display_text(raw_bytes.decode('utf-8'))
    target = path
    if working_root is not None and relative_path is not None:
        target = _resolved_hint_target(path, working_root)
        expected = render_hint_preview(normalized, relative_path)
        if target is None or _scan_context_content(normalized, path.name) != normalized:
            return  # A blocked diagnostic never attests the rejected raw source.
    else:
        expected = normalized
    if expected != presented_text:
        return
    try:
        grant = context.registry.issue_file_slice(
            path=target, line_start=1, line_end=raw_bytes.count(b'\n') + int(not raw_bytes.endswith(b'\n')),
            content=raw_bytes, session_id=context.session_id,
            turn_id=context.turn_id, request_id=context.request_id,
            policy_digest=context.policy_digest,
        )
        if working_root is not None and relative_path is not None:
            context.registry.remember_context_read(grant, working_root, relative_path)
    except (OSError, SourceProvenanceError):
        # No proof is issued if canonical bytes, path policy, or secret checks fail.
        return


def source_presentation_matches(segment, grant, raw_text):
    """Validate each deterministic rendering against its independently verified slice."""
    from agent.source_context_rendering import PREVIEW_KIND, render_hint_preview

    if not isinstance(segment.presentation_kind, str):
        return False
    if segment.presentation_kind.startswith(PREVIEW_KIND):
        relative = segment.presentation_kind[len(PREVIEW_KIND):]
        return grant.line_start == 1 and segment.text == render_hint_preview(raw_text, relative)
    if segment.presentation_kind == CONTEXT_TEXT:
        from agent.subdirectory_hints import _HINT_FILENAMES

        return (grant.line_start == 1 and grant.canonical_path.name in _HINT_FILENAMES
                and segment.text == context_display_text(raw_text))
    if segment.presentation_kind != 'read_file_json_v1':
        return False
    try:
        parsed = json.loads(segment.text)
    except (TypeError, ValueError):
        return False
    expected = {
        '\n'.join(f'{number}|{line}' for number, line in enumerate(lines, start=grant.line_start))
        for lines in (raw_text.splitlines(), raw_text.split('\n'))
    }
    return isinstance(parsed, dict) and parsed.get('content') in expected


def _current_presentation_grants(metadata, grant_texts, registry, identity):
    """Recover exact current identities lost by the generic text-deduplicated view."""
    from agent.llm_egress_terminal import _read_grant_text

    if metadata.get('request_id') != identity.get('request_id'):
        return {}
    owner = tuple(identity.get(key) for key in ('session_id', 'turn_id', 'request_id', 'policy_digest'))
    allowed = {value for value in metadata['source_grant_digests'] if isinstance(value, str)}
    texts = {source_grant_digest(grant): raw for raw, grant in grant_texts}
    issued = (registry.grants_for_request(identity['request_id']) if registry is not None
              else tuple(grant for _, grant in grant_texts))
    current = {}
    for grant in issued:
        digest = source_grant_digest(grant)
        if (digest not in allowed or
                (grant.session_id, grant.turn_id, grant.request_id, grant.policy_digest) != owner):
            continue
        raw = texts.get(digest)
        if raw is None:
            raw = _read_grant_text(grant)
        if raw is None:
            continue
        encoded = raw.encode('utf-8')
        if len(encoded) != grant.byte_count or sha256(encoded).hexdigest() != grant.content_sha256:
            continue
        current[digest] = (raw, grant)
    return current


def _guidance_grants(metadata, grant_texts, registry, identity):
    """A replay must re-verify an earlier validated grant under the same owner turn."""
    from agent.llm_egress_terminal import _read_grant_text

    allowed = metadata.get('source_grant_digests')
    if not isinstance(allowed, (tuple, list)):
        return {}
    current = _current_presentation_grants(metadata, grant_texts, registry, identity)
    selected = {}
    for digest in allowed:
        if not isinstance(digest, str):
            continue
        if digest in current:
            selected[digest] = current[digest]
        elif registry is not None:
            rebound = registry.rebind_validated_presentation(
                digest, original_request_id=metadata.get('request_id'), **identity,
            )
            if rebound is not None and (text := _read_grant_text(rebound)) is not None:
                selected[digest] = (text, rebound)
    return selected


def _hint_piece(remaining, grants, seen, source_path, *, registry, metadata, identity):
    """Match the whole displayed hint, not a filename or a source substring."""
    match = _HEADER.match(remaining)
    if match is None:
        return None
    relative = PurePosixPath(match[1])
    if (relative.is_absolute() or '\\' in match[1] or '..' in relative.parts
            or str(relative) != match[1]):
        return None
    header = match[0]
    candidates = []
    for digest, (raw, grant) in grants.items():
        from agent.source_context_rendering import PREVIEW_KIND, render_hint_preview

        parts = relative.parts
        if digest in seen:
            continue
        bindings = (registry.context_read_bindings(digest, metadata.get('request_id'), identity)
                    if registry is not None else ())
        bound = any(binding.relative_path == match[1] and binding.matches(grant, source_path)
                    for binding in bindings)
        canonical = (grant.canonical_path.parts[-len(parts):] == parts
                     and source_path.is_relative_to(grant.canonical_path.parent))
        if not bound and not canonical:
            continue
        body = render_hint_preview(raw, match[1]) if bound else context_display_text(raw)
        piece = header + body
        kind = PREVIEW_KIND + match[1] if bound else CONTEXT_TEXT
        presentation = SourcePresentationSegment(source_grant_digest(grant), body, kind)
        tail = remaining[len(piece):]
        if (source_presentation_matches(presentation, grant, raw)
                and remaining.startswith(piece) and (not tail or _HEADER.match(tail))):
            candidates.append((digest, grant, header, presentation, len(piece)))
    return candidates[0] if len(candidates) == 1 else None


def segment_read_with_context(text, metadata, grant_texts, used_grants, *, registry=None,
                              session_id='', turn_id='', request_id='', policy_digest=''):
    """Preserve wire bytes while requiring one exact proof for each source component."""
    from agent.source_read_presentations import _segment_read_file_presentation

    denied = UntrustedProvenanceSegment(sha256(text.encode('utf-8')).hexdigest())
    if metadata.get('content_sha256') != sha256(text.encode('utf-8')).hexdigest():
        return denied
    try:
        _, end = json.JSONDecoder().raw_decode(text)
    except (TypeError, ValueError):
        return denied
    if not _HEADER.match(text, end):
        return denied
    prefix, remaining = text[:end], text[end:]
    identity = dict(session_id=session_id, turn_id=turn_id, request_id=request_id,
                    policy_digest=policy_digest)
    from agent.source_read_results import READ_RESULT_ID, select_producer_read, select_complete_result
    from agent.source_read_presentations import prefix_metadata

    record = select_producer_read(text, metadata, registry, identity) if READ_RESULT_ID in metadata else None
    if READ_RESULT_ID in metadata and record is None:
        return denied
    collected = {}
    # Re-verify each original proof once, then share those current-request
    # grants between the JSON prefix and guidance parsers.
    grants = _guidance_grants(metadata, grant_texts, registry, identity)
    prefix_grants = tuple(grants.values())
    if record is not None:
        exact = grants.get(source_grant_digest(record.grant))
        if exact is None:
            return denied
        prefix_grants = (exact,)
    first = _segment_read_file_presentation(
        prefix, prefix_metadata(metadata, prefix, request_id, prefix_grants), prefix_grants,
        collected, registry=registry, **identity,
    )
    if not isinstance(first, SourcePresentationSegment):
        return denied
    segments, seen = [first], set()
    while remaining:
        from agent.source_context_diagnostic import NOTICE_KIND, blocked_notice_piece

        header_match = _HEADER.match(remaining)
        diagnostic = blocked_notice_piece(remaining, header_match) if header_match else None
        if diagnostic is not None:
            body, length = diagnostic
            segments.extend((SanitizedSegment(header_match[0]), ValidatedToolSyntaxSegment(body, NOTICE_KIND)))
            remaining = remaining[length:]
            continue
        piece = _hint_piece(remaining, grants, seen, collected[first.source_grant_digest].canonical_path,
                            registry=registry, metadata=metadata, identity=identity)
        if piece is None:
            return denied
        digest, grant, header, presentation, length = piece
        seen.add(digest)
        collected[source_grant_digest(grant)] = grant
        segments.extend((SanitizedSegment(header), presentation))
        remaining = remaining[length:]
    if record is not None and not select_complete_result(used_grants, record, request_id, first.source_grant_digest):
        return denied
    used_grants.update(collected)
    return OutboundText(tuple(segments))


def matching_read_grants(text, metadata, grant_texts):
    """Match the existing JSON/numbered read rendering, independent of appended guidance."""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None, set(), []
    if not isinstance(parsed, dict) or not isinstance(parsed.get('content'), str):
        return None, set(), []
    allowed = {value for value in metadata['source_grant_digests'] if isinstance(value, str)}
    candidates = []
    for raw_text, grant in grant_texts:
        digest = source_grant_digest(grant)
        if digest not in allowed or metadata.get('request_id') != grant.request_id:
            continue
        expected = '\n'.join(f'{number}|{line}' for number, line in
                             enumerate(raw_text.splitlines(), start=grant.line_start))
        if parsed['content'] == expected:
            candidates.append((digest, grant))
    return parsed['content'], allowed, candidates


def context_display_text(text: str) -> str:
    """Match utf-8-sig/read_text universal-newline display without changing source bytes."""
    return text.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n').strip()


def read_context_bytes(path: Path) -> bytes | None:
    """Keep the existing hint read deadline while retaining the bytes actually read."""
    from agent.prompt_builder import _get_context_file_read_timeout
    from agent.memory_provider import spawn_context_thread

    timeout = _get_context_file_read_timeout()
    result = queue.Queue(maxsize=1)

    def read():
        try:
            result.put((True, path.read_bytes()))
        except OSError as exc:
            result.put((False, exc))

    spawn_context_thread(read, name=f'context-read:{path.name}').start()
    try:
        ok, value = result.get(timeout=timeout)
    except queue.Empty:
        logging.getLogger(__name__).warning('Context file %s read timed out after %.1fs; skipping', path, timeout)
        return None
    if ok:
        return value
    raise value
