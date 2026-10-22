"""Select only the exact producer grant; never resolve read identity by equal contents."""
from hashlib import sha256
from collections.abc import Mapping
from typing import Any, Sequence

from agent.llm_egress_firewall import (
    SourceGrant, SourcePresentationSegment, OutboundText, UntrustedProvenanceSegment, source_grant_digest,
)
from agent.source_provenance import SourceProvenanceRegistry
from agent.source_read_results import READ_RESULT_ID, select_complete_result, select_producer_read


def prefix_metadata(metadata, prefix, request_id, grants):
    return {
        **{key:value for key, value in metadata.items() if key != READ_RESULT_ID},
        'presentation_kind':'read_file_json_v1',
        'content_sha256':sha256(prefix.encode('utf-8')).hexdigest(),
        'request_id':request_id,
        'source_grant_digests':tuple(source_grant_digest(grant) for _, grant in grants),
    }


def segment_bound_read(text, metadata, grant_texts, used_grants, *, registry, **identity):
    from agent.source_context_presentation import _guidance_grants

    denied = UntrustedProvenanceSegment(sha256(text.encode('utf-8')).hexdigest())
    if metadata.get('presentation_kind') != 'read_file_json_v1':
        return denied
    record = select_producer_read(text, metadata, registry, identity)
    if record is None:
        return denied
    grants = _guidance_grants(metadata, grant_texts, registry, identity)
    exact = grants.get(source_grant_digest(record.grant))
    if exact is None:
        return denied
    collected = {}
    segment = _segment_read_file_presentation(
        text, prefix_metadata(metadata, text, identity['request_id'], (exact,)),
        (exact,), collected, registry=registry, **identity,
    )
    if (not isinstance(segment, SourcePresentationSegment)
            or not select_complete_result(used_grants, record, identity['request_id'], segment.source_grant_digest)):
        return denied
    used_grants.update(collected)
    return segment


def _segment_read_file_presentation(
    text: str,
    metadata: Any,
    grant_texts: Sequence[tuple[str, SourceGrant]],
    used_grants: dict[str, SourceGrant],
    *,
    registry: SourceProvenanceRegistry | None = None,
    session_id: str = "",
    turn_id: str = "",
    request_id: str = "",
    policy_digest: str = "",
) -> SourcePresentationSegment | OutboundText | UntrustedProvenanceSegment:
    """Bind the real JSON/line-number presentation to one exact read grant."""

    from agent.source_grant_text import _read_grant_text
    from agent.source_context_presentation import (
        READ_WITH_CONTEXT, segment_read_with_context, matching_read_grants,
    )

    if isinstance(metadata, Mapping) and metadata.get("presentation_kind") == READ_WITH_CONTEXT:
        return segment_read_with_context(
            text, metadata, grant_texts, used_grants, registry=registry, session_id=session_id,
            turn_id=turn_id, request_id=request_id, policy_digest=policy_digest,
        )
    if isinstance(metadata, Mapping) and 'read_result_binding_id' in metadata:
        return segment_bound_read(text, metadata, grant_texts, used_grants, registry=registry,
                                  session_id=session_id, turn_id=turn_id, request_id=request_id,
                                  policy_digest=policy_digest)
    denied = UntrustedProvenanceSegment(sha256(text.encode("utf-8")).hexdigest())
    if not isinstance(metadata, Mapping):
        return denied
    if metadata.get("presentation_kind") != "read_file_json_v1":
        return denied
    if metadata.get("content_sha256") != sha256(text.encode("utf-8")).hexdigest():
        return denied
    digests = metadata.get("source_grant_digests")
    if not isinstance(digests, (list, tuple)) or not digests:
        return denied
    content, allowed_digests, candidates = matching_read_grants(text, metadata, grant_texts)
    if content is None:
        return denied
    if not candidates and registry is not None:
        original_request_id = metadata.get("request_id")
        if isinstance(original_request_id, str):
            for original_digest in allowed_digests:
                rebound = registry.rebind_validated_presentation(
                    original_digest,
                    original_request_id=original_request_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    request_id=request_id,
                    policy_digest=policy_digest,
                )
                from agent.source_context_preview import ContextPreviewGrant
                if rebound is None or isinstance(rebound, ContextPreviewGrant):
                    continue
                raw_text = _read_grant_text(rebound)
                if raw_text is None:
                    continue
                expected = "\n".join(
                    f"{line_number}|{line}"
                    for line_number, line in enumerate(
                        raw_text.splitlines(), start=rebound.line_start
                    )
                )
                if content == expected:
                    candidates.append((source_grant_digest(rebound), rebound))
    if len(candidates) != 1:
        return denied
    digest, grant = candidates[0]
    used_grants[digest] = grant
    return SourcePresentationSegment(digest, text, "read_file_json_v1")
