"""Trusted tool-boundary adapters for source provenance.

The large tool dispatch and file-tool modules call these narrow adapters but
do not own the security policy or grant-construction responsibilities.
"""

from __future__ import annotations

from contextlib import nullcontext
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from typing import Any

from agent.source_provenance import active_source_provenance
from agent import source_provenance_diagnostics as diagnostics

_TRUSTED_READ_TOOL_NAMES = {"read_file", "read"}


def source_provenance_activation(agent: Any, function_name: str, *, tool_call_id: str = ''):
    """Activate request identity only around the trusted ``read_file`` tool."""

    if function_name not in _TRUSTED_READ_TOOL_NAMES:
        return nullcontext()
    owner = ("", "", "", "")
    registry = None
    try:
        from agent.source_provenance import (
            DEFAULT_POLICY_DIGEST,
            SourceProvenanceRegistry,
            activate_source_provenance,
            following_api_request_id,
            source_provenance_registry_for_agent,
        )

        session_id = str(getattr(agent, "session_id", "") or "")
        turn_id = str(getattr(agent, "_current_turn_id", "") or "")
        request_id = following_api_request_id(
            str(getattr(agent, "_current_api_request_id", "") or ""), turn_id
        )
        policy_digest = str(
            getattr(agent, "_llm_egress_policy_digest", "")
            or getattr(agent, "llm_egress_policy_digest", "")
            or DEFAULT_POLICY_DIGEST
        )
        owner = (session_id, turn_id, request_id, policy_digest)
        if not all(owner):
            return diagnostics.activation(agent, owner, registry, nullcontext(), "missing_identity")
        registry = source_provenance_registry_for_agent(agent)
        if not isinstance(registry, SourceProvenanceRegistry):
            return diagnostics.activation(agent, owner, registry, nullcontext(), "no_registry")
        context = activate_source_provenance(
            registry,
            session_id=session_id,
            turn_id=turn_id,
            request_id=request_id,
            policy_digest=policy_digest,
            tool_call_id=tool_call_id,
        )
        return diagnostics.activation(agent, owner, registry, context, "active")
    except Exception:
        return diagnostics.activation(agent, owner, registry, nullcontext(), "unexpected_error")


def execute_with_source_provenance(agent, function_name, tool_call_id, execute, arguments):
    """Dispatcher execution adapter: preserve the actual call label in the read context."""
    with source_provenance_activation(agent, function_name, tool_call_id=tool_call_id):
        return execute(arguments)


def attach_trusted_source_provenance_metadata(
    agent: Any,
    function_name: str,
    *,
    content: Any | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any] | None:
    """Return an opaque, content-bound trusted-read envelope.

    The envelope is carried only in the internal tool message and consumed by
    the egress runtime.  It binds the exact presentation bytes to grants for
    the exact following provider request; it is never serialized to a provider.
    """

    if function_name not in _TRUSTED_READ_TOOL_NAMES:
        return None
    owner = ("", "", "", "")
    registry = None
    try:
        from agent.llm_egress_firewall import source_grant_digest
        from agent.source_provenance import (
            DEFAULT_POLICY_DIGEST,
            SourceProvenanceRegistry,
            following_api_request_id,
        )

        registry = getattr(agent, "_source_provenance_registry", None)
        turn_id = str(getattr(agent, "_current_turn_id", "") or "")
        request_id = following_api_request_id(
            str(getattr(agent, "_current_api_request_id", "") or ""), turn_id
        )
        owner = (str(getattr(agent, "session_id", "") or ""), turn_id, request_id,
                 str(getattr(agent, "_llm_egress_policy_digest", "")
                     or getattr(agent, "llm_egress_policy_digest", "") or DEFAULT_POLICY_DIGEST))
        if not isinstance(registry, SourceProvenanceRegistry) or not request_id:
            diagnostics.record(agent, owner, registry, "commit", "no_registry" if not isinstance(
                registry, SourceProvenanceRegistry) else "no_request")
            return None
        digests = tuple(
            source_grant_digest(grant)
            for grant in registry.grants_for_request(request_id)
        )
        if not digests:
            diagnostics.record(agent, owner, registry, "commit", "no_grants")
            return None
        metadata = getattr(agent, "_source_provenance_metadata", None)
        if not isinstance(metadata, dict):
            metadata = {}
            agent._source_provenance_metadata = metadata
        envelope: dict[str, Any] = {
            "request_id": request_id,
            "source_grant_digests": digests,
        }
        if isinstance(content, str):
            envelope["content_sha256"] = sha256(content.encode("utf-8")).hexdigest()
            from agent.source_context_presentation import presentation_kind

            envelope["presentation_kind"] = presentation_kind(content)
        if tool_call_id is not None:
            from agent.source_read_results import READ_RESULT_ID

            owner = (str(getattr(agent, 'session_id', '') or ''), turn_id, request_id,
                     str(getattr(agent, '_llm_egress_policy_digest', '')
                         or getattr(agent, 'llm_egress_policy_digest', '') or DEFAULT_POLICY_DIGEST))
            envelope[READ_RESULT_ID] = registry.commit_read_result(owner, tool_call_id, content)
        metadata[request_id] = envelope
        binding_present = bool(envelope.get("read_result_binding_id"))
        diagnostics.record(agent, owner, registry, "commit", "metadata_committed",
                           binding_present=binding_present)
        if tool_call_id is not None and not binding_present:
            diagnostics.record(agent, owner, registry, "commit", "binding_rejected")
        return envelope
    except Exception:
        diagnostics.record(agent, owner, registry, "commit", "unexpected_error")
        return None


def build_source_provenance_sidecar(messages: Any) -> list[dict[str, Any]]:
    """Extract bounded content-free provenance before wire conversion strips it."""

    if not isinstance(messages, list):
        return []
    sidecar: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        metadata = message.get("_source_provenance")
        content = message.get("content")
        if (
            message.get("role") != "tool"
            or not isinstance(metadata, dict)
            or not isinstance(content, str)
            or metadata.get("content_sha256")
            != sha256(content.encode("utf-8")).hexdigest()
        ):
            continue
        sidecar.append(
            {
                **metadata,
                "message_index": index,
                "tool_call_id": message.get("tool_call_id"),
            }
        )
    return sidecar


def issue_active_read_provenance(
    *,
    resolved: Path | PurePosixPath,
    source_path: Path | None,
    offset: int,
    limit: int,
    returned_content: str,
    result_dict: dict[str, Any],
    file_ops: Any,
) -> None:
    """Issue provenance only for a verified local ``read_file`` result."""

    context = active_source_provenance()
    if context is None or not isinstance(resolved, Path) or source_path is None:
        diagnostics.producer("no_context" if context is None else "ineligible_backend")
        return
    if result_dict.get("error") or result_dict.get("truncated_by") or not returned_content:
        diagnostics.producer("result_ineligible")
        return
    try:
        canonical = resolved.resolve(strict=True)
        if resolved.is_symlink() or not canonical.is_file():
            diagnostics.producer("source_unavailable")
            return
        selected: list[bytes] = []
        with canonical.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if line_number < offset:
                    continue
                if line_number >= offset + limit:
                    break
                selected.append(raw_line)
        if not selected:
            diagnostics.producer("empty_slice")
            return
        raw = b"".join(selected)
        decoded = raw.decode("utf-8")
        from tools.tool_output_limits import get_max_line_length

        if any(len(line) > get_max_line_length() for line in decoded.split("\n")):
            diagnostics.producer("line_limit")
            return
        if returned_content != file_ops._add_line_numbers(decoded, offset):
            diagnostics.producer("presentation_mismatch")
            return
        grant = context.registry.issue_file_slice(
            path=source_path,
            line_start=offset,
            line_end=offset + len(selected) - 1,
            content=raw,
            session_id=context.session_id,
            turn_id=context.turn_id,
            request_id=context.request_id,
            policy_digest=context.policy_digest,
        )
        diagnostics.producer("grant_issued")
        if context.tool_call_id:
            context.registry.remember_producer_read(
                grant, context.tool_call_id,
                sha256(json.dumps(result_dict, ensure_ascii=False).encode('utf-8')).hexdigest(),
            )
    except UnicodeDecodeError:
        diagnostics.producer("non_utf8")
    except Exception as exc:  # health: allow BLE001 -- Preserve existing fail-closed producer behavior without logging private exception text.
        diagnostics.producer_exception(exc)


__all__ = [
    "attach_trusted_source_provenance_metadata",
    "build_source_provenance_sidecar",
    "execute_with_source_provenance",
    "issue_active_read_provenance",
    "source_provenance_activation",
]
