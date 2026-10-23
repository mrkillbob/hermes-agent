"""Final provider-boundary enforcement for source-bound LLM egress."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shlex
from hashlib import sha256
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

from agent.source_grant_text import _read_grant_text
from agent.remote_task_context import owned_remote_task, require_public_task_context, mark_local_task_context
from agent.llm_egress_firewall import (
    AuthorizedEgress,
    CodexReasoningReplaySegment,
    EgressBlocked,
    LLMEgressFirewall,
    LiteralSegment,
    OutboundText,
    SanitizedSegment,
    SourceBoundSegment,
    SourcePresentationSegment,
    SourceGrant,
    TypedOutboundRequest,
    UntrustedProvenanceSegment,
    ValidatedToolSyntaxSegment,
    DestinationClass,
    GeneratedContextKey,
    GeneratedContextSegment,
    classify_destination,
    source_grant_digest,
    static_literal_sha256,
    validate_sanitized_text,
    content_free_violation_locations,
    redact_remote_unsafe_text,
    validate_tool_syntax,
)
from agent.llm_egress_classifier import (
    _REMOTE_KANBAN_ATTACHMENT_TOOL_NAMES,
    _REMOTE_KANBAN_LIFECYCLE_TOOL_NAMES,
    _REMOTE_KANBAN_TOOL_SEARCH_PROJECTION_TOOL_NAMES,
    _structural_literal_hashes,
    _structured_tool_output_text,
    _typed_payload,
    _typed_payload_violation_locations,
)
from agent.llm_egress_terminal import (
    _REMOTE_KANBAN_FILE_MUTATION_REPLAY_TOOL_NAMES,
    _REMOTE_KANBAN_PROJECTION_TOOL_NAMES,
    _REMOTE_KANBAN_READ_FILE_PROJECTION_TOOL_NAMES,
    _REMOTE_KANBAN_SEARCH_PROJECTION_TOOL_NAMES,
    _REMOTE_KANBAN_TERMINAL_REPLAY_TOOL_NAMES,
    _REMOTE_KANBAN_WEB_REPLAY_TOOL_NAMES,
    _combined_github_list_terminal_call_limits,
    _combined_github_view_terminal_call_limits,
    _git_diff_name_only_terminal_call_ids,
    _git_grep_terminal_call_ids,
    _git_review_summary_terminal_call_ids,
    _git_workspace_diagnostic_call_ids,
    _github_api_curl_terminal_call_ids,
    _github_api_extract_call_limits,
    _github_api_paginate_terminal_call_limits,
    _github_list_terminal_call_limits,
    _github_pr_feedback_terminal_call_ids,
    _kanban_assignees_terminal_call_ids,
    _plain_github_list_terminal_call_ids,
    _pytest_terminal_call_ids,
    _recognized_syntax_tool_call_ids,
    _recognized_tool_call_ids,
    _rejected_terminal_call_ids,
    _rg_terminal_call_ids,
    _scratch_read_file_tool_call_ids,
)
from agent.llm_egress_cron import project_cron_list
from agent.llm_egress_tool_diagnostics import type_tool_diagnostics
from agent.llm_egress_schema import type_builtin_schema_words
from agent.message_sanitization import tool_result_id_variants
from agent.redact import redact_sensitive_text
from agent.source_provenance import DEFAULT_POLICY_DIGEST, SourceProvenanceError, SourceProvenanceRegistry
from agent.source_read_results import SourceReadSelection, remember_validated_sources


# Timeout is a non-content SDK control. Header/query values remain in the
# authorized JSON body so credentials or other caller-controlled text cannot
# be appended after the firewall receipt is written.
_SDK_CONTROL_KEYS = frozenset({"timeout"})
_INTERNAL_EGRESS_KEYS = frozenset({"_hermes_source_provenance"})
_PROTECTED_REMOTE_PROVIDERS = frozenset({
    "anthropic",
    "openai-codex",
    "nous",
    "nous-portal",
    "nousresearch",
})
logger = logging.getLogger(__name__)

# Local action results are safe to replay only as bounded outcomes, and only
# when the result is bound to the exact preceding call.  Browser Use runs
# through ``browser_exec`` rather than ``terminal``; omitting it here makes a
# protected worker treat its own browser result as untrusted provenance and
# fail on otherwise harmless page identifiers or encoded-looking text.
# Both catalog bridge calls return model-readable tool schemas/descriptions.
# Protected workers must replay only the bounded local outcome; otherwise a
# tool_describe result is treated as untrusted provider content and can trip
# the egress firewall on harmless schema words.
_GITHUB_API_PAGINATE_ARGUMENT_REPLAY = (
    '{"command":"gh api --paginate GitHub REST list (details omitted)"}'
)














_CREDENTIAL_ENV_SUFFIXES = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
    "_KEY",
    "_PASSWORD",
    "_CREDENTIAL",
)

_PRIVATE_PATH_IN_TEXT = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"/(?:Users|home|private|var/folders|root|Volumes)/[^\s\"'`)]+"
    r"|~(?:/|\\)[^\s\"'`)]+"
    r"|[A-Za-z]:\\+(?:Users|Documents and Settings)\\+[^\s\"'`)]+"
    r")",
    re.IGNORECASE,
)


def _prepare_bound_skill_source_results(
    body: Mapping[str, Any], registry: SourceProvenanceRegistry,
    identity: tuple[str, str, str, str],
) -> Mapping[str, Any]:
    """Replay exact local skill instructions, never a tool-supplied envelope.

    Only a preceding unique skill_view call may authorize the trusted local
    resolver's bounded source file. No source path from the result is trusted.
    Preprocessed/linked/plugin results without this proof remain fail-closed.
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body
    from tools.skills_tool import locate_skill, skill_lookup_path_error, skill_search_dirs

    all_ids = [call.get("id") for message in messages if isinstance(message, Mapping)
               and message.get("role") == "assistant"
               for call in (message.get("tool_calls", ()) or ()) if isinstance(call, Mapping)]
    duplicates = {item for item in all_ids if isinstance(item, str) and all_ids.count(item) > 1}
    calls: dict[str, Mapping[str, Any] | None] = {}
    seen: set[str] = set()
    projected = []
    for message in messages:
        if not isinstance(message, Mapping):
            projected.append(message)
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", ()) or ():
                if not isinstance(call, Mapping):
                    continue
                call_id = call.get("id")
                function = call.get("function")
                if not isinstance(call_id, str):
                    continue
                if call_id in seen:
                    calls[call_id] = None
                    continue
                seen.add(call_id)
                if isinstance(function, Mapping) and function.get("name") == "skill_view":
                    try:
                        args = json.loads(function.get("arguments", ""))
                    except (TypeError, ValueError):
                        args = None
                    calls[call_id] = args if isinstance(args, Mapping) else None
        # Chat-completions wire adapters omit the optional producer label.
        # Infer only from an exact unique preceding call; never from payload.
        bound_args = calls.get(message.get("tool_call_id"))
        labels = (message.get("tool_name"), message.get("name"))
        if message.get("role") != "tool" or not (
            isinstance(bound_args, Mapping) or "skill_view" in labels
        ):
            projected.append(message)
            continue
        copied = dict(message)
        raw = message.get("content")
        # A content-free marker forces the existing final provenance gate to
        # reject every mismatch without leaking tool bytes or local paths.
        copied["content"] = UntrustedProvenanceSegment(
            sha256(str(raw).encode("utf-8")).hexdigest()
        )
        try:
            if any(label not in (None, "skill_view") for label in labels):
                raise ValueError("conflicting_skill_producer")
            args = bound_args
            if not isinstance(args, Mapping) or args.get("file_path") or message.get("tool_call_id") in duplicates:
                raise ValueError("unbound_skill")
            name = args.get("name")
            if not isinstance(name, str) or ":" in name or skill_lookup_path_error(name):
                raise ValueError("unsupported_skill")
            payload = json.loads(raw) if isinstance(raw, str) else None
            if not isinstance(payload, Mapping) or payload.get("success") is not True or payload.get("name") != name:
                raise ValueError("invalid_result")
            roots, _ = skill_search_dirs()
            error, _, source = locate_skill(name, None, roots)
            if error is not None or source is None:
                raise ValueError("missing_source")
            source = Path(source)
            if Path(str(payload.get("_source_path", ""))).absolute() != source.absolute():
                raise ValueError("source_mismatch")
            from agent.source_provenance import MAX_SOURCE_SLICE_BYTES
            if source.stat().st_size > MAX_SOURCE_SLICE_BYTES:
                raise ValueError("source_too_large")
            with source.open("rb") as handle:
                data = handle.read(MAX_SOURCE_SLICE_BYTES + 1)
            if len(data) > MAX_SOURCE_SLICE_BYTES:
                raise ValueError("source_too_large")
            text = data.decode("utf-8")
            result_text = payload.get("content")
            if not isinstance(result_text, str) or not result_text or result_text not in text:
                raise ValueError("content_mismatch")
            grant = registry.issue_file_slice(
                path=source, line_start=1, line_end=len(text.splitlines()), content=data,
                session_id=identity[0], turn_id=identity[1], request_id=identity[2],
                policy_digest=identity[3],
            )
            copied["content"] = SourceBoundSegment(source_grant_digest(grant))
        except (OSError, ValueError, TypeError, UnicodeError, SourceProvenanceError):
            pass
        projected.append(copied)
    return {**body, "messages": projected}


def _sanitize_protected_kanban_body(value: Any) -> Any:
    """Remove host paths from protected Kanban tool results before typing.

    This deliberately does not rewrite secrets or arbitrary encoded content;
    those remain visible to the fail-closed firewall scans and are denied.
    """

    if isinstance(value, str):
        text = value
        for name in (
            "HERMES_KANBAN_CLAIM_LOCK",
            "HERMES_KANBAN_RUN_ID",
            "HERMES_SESSION_ID",
            "HERMES_STREAM_STALE_GIVEUP",
            "HERMES_TURN_LEASE_TIMEOUT",
        ):
            raw = os.environ.get(name)
            if raw:
                text = re.sub(
                    rf"(?m)^(?P<label>{re.escape(name)}=){re.escape(raw)}$",
                    rf"\g<label>${name}",
                    text,
                )
        replacements = (
            (os.environ.get("HERMES_KANBAN_WORKSPACE"), "."),
            (os.environ.get("HERMES_KANBAN_WORKSPACES_ROOT"), "$HERMES_KANBAN_WORKSPACES_ROOT"),
            (os.environ.get("HERMES_KANBAN_DB"), "$HERMES_KANBAN_DB"),
            (os.environ.get("HERMES_CONTROL_HOME"), "$HERMES_CONTROL_HOME"),
            (os.environ.get("HERMES_HOME"), "$HERMES_PROFILE_HOME"),
        )
        for raw, token in sorted(
            ((raw, token) for raw, token in replacements if raw),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            text = text.replace(raw, token)
        return _PRIVATE_PATH_IN_TEXT.sub("<private-path>", text)
    if isinstance(value, Mapping):
        return {
            _sanitize_protected_kanban_body(key): _sanitize_protected_kanban_body(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_protected_kanban_body(item) for item in value]
    return value


def provider_uses_egress_firewall(provider: Any) -> bool:
    """Return whether an exact configured provider owns a protected remote lane."""

    return str(provider or "").strip().lower() in _PROTECTED_REMOTE_PROVIDERS


def egress_enforcement_enabled() -> bool:
    """Return the operator-controlled LLM egress enforcement posture.

    Enforcement remains enabled by default.  The explicit temporary disable
    switch preserves the firewall and its diagnostics while allowing operator
    testing to continue until the false-positive cases are repaired.
    """
    try:
        from hermes_cli import managed_scope
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly()
        if managed_scope.is_key_managed("runtime.llm_egress_enforcement"):
            posture = str((config.get("runtime") or {}).get("llm_egress_enforcement", "enabled"))
            return posture.strip().lower() not in {"0", "false", "off", "disabled", "disable", "monitor"}
    except Exception:
        pass
    try:
        from hermes_cli.config import load_config_readonly

        runtime = load_config_readonly().get("runtime") or {}
        posture = str(runtime.get("llm_egress_enforcement", "enabled"))
        return posture.strip().lower() not in {"0", "false", "off", "disabled", "disable", "monitor"}
    except Exception:
        # A malformed or unavailable config must not silently weaken the boundary.
        return True


# Benchmark-backed per-profile model route table, installed at startup by
# install_performance_route_table() when a route artifact is configured.
_PERFORMANCE_ROUTE_TABLE: Any | None = None


def install_performance_route_table(table: Any) -> None:
    """Activate a compiled benchmark-backed route table for _route_for_agent.

    Call once at startup with the result of
    ``agent.model_performance_router.compile_profile_routes`` (or
    ``hermes_cli.profile_route_compiler.compile_profile_configs``).  The table
    is keyed by profile name then surface name; ``_route_for_agent`` consults
    it when the agent exposes a ``performance_surface`` attribute.
    """
    global _PERFORMANCE_ROUTE_TABLE
    _PERFORMANCE_ROUTE_TABLE = table


def _exact_provider_secret_values() -> tuple[str, ...]:
    """Snapshot exact profile and credential environment values before send.

    This is the final provider-boundary interlock for the exact applied-secret
    class tracked in #77165; shape-based redaction remains an independent scan.
    """

    try:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
    except Exception:
        home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    try:
        from hermes_cli.env_loader import get_secret_source_values

        values = list(get_secret_source_values(home).values())
    except Exception:
        values = []
    values.extend(
        value
        for name, value in os.environ.items()
        if value and name.upper().endswith(_CREDENTIAL_ENV_SUFFIXES)
    )
    return tuple(
        dict.fromkeys(value for value in values if isinstance(value, str) and value)
    )




def _grant_texts(grants: Sequence[SourceGrant]) -> tuple[tuple[str, SourceGrant], ...]:
    from agent.source_context_preview import ContextPreviewGrant
    unique: dict[str, SourceGrant] = {}
    for grant in grants:
        if not isinstance(grant, (SourceGrant, ContextPreviewGrant)):
            continue
        text = _read_grant_text(grant)
        if text:
            unique.setdefault(text, grant)
    return tuple(sorted(unique.items(), key=lambda item: (-len(item[0]), item[0])))


































































































def _route_for_agent(agent: Any, route: Any | None) -> Any:
    if route is not None:
        return route
    # Consult the benchmark-backed performance route table when available and
    # the agent declares a surface.  Falls through to agent defaults on miss.
    surface = str(getattr(agent, "performance_surface", "") or "")
    if _PERFORMANCE_ROUTE_TABLE is not None and surface:
        try:
            from agent.model_performance_router import resolve_route
            profile = str(getattr(agent, "profile", "") or "default")
            privacy = str(getattr(agent, "privacy_class", "") or "sanitized")
            return resolve_route(
                _PERFORMANCE_ROUTE_TABLE,
                profile=profile,
                surface=surface,
                privacy=privacy,
                required_context=0,
            )
        except Exception:
            pass
    provider = str(getattr(agent, "provider", "") or "")
    base_url = getattr(agent, "base_url", None)
    api_mode = getattr(agent, "api_mode", None)
    if provider == "openai-codex" and not base_url:
        base_url = "https://chatgpt.com/backend-api/codex"
        api_mode = api_mode or "codex_responses"
    return SimpleNamespace(
        provider=provider,
        model=str(getattr(agent, "model", "") or ""),
        base_url=base_url,
        api_mode=api_mode,
    )


def _route_field(route: Any, name: str, default: Any = None) -> Any:
    """Read route fields from both provider objects and serialized mappings."""

    if isinstance(route, Mapping):
        return route.get(name, default)
    return getattr(route, name, default)


def _restore_source_provenance_sidecar(
    body: Mapping[str, Any], sidecar: Any
) -> dict[str, Any]:
    """Reattach exact content-bound metadata after either wire conversion.

    Chat Completions retains tool messages, while Codex Responses converts
    them to ``function_call_output`` items.  The latter must recover the same
    internal envelope before typing; otherwise a verified read is mistaken
    for untrusted structured output and silently elided.
    """

    restored = dict(body)
    messages = restored.get("messages")
    if not isinstance(sidecar, list):
        return restored
    consumed_entries: set[int] = set()
    if isinstance(messages, list):
        copied_messages = list(messages)
        matches: dict[int, list[int]] = {}
        for entry_index, entry in enumerate(sidecar):
            if not isinstance(entry, Mapping):
                continue
            if entry_index in consumed_entries:
                continue
            index = entry.get("message_index")
            if not isinstance(index, int) or isinstance(index, bool):
                continue
            if index < 0 or index >= len(copied_messages):
                continue
            message = copied_messages[index]
            if not isinstance(message, Mapping):
                continue
            content = message.get("content")
            if (
                message.get("role") != "tool"
                or not isinstance(content, str)
                or message.get("tool_call_id") != entry.get("tool_call_id")
                or entry.get("content_sha256")
                != sha256(content.encode("utf-8")).hexdigest()
            ):
                continue
            matches.setdefault(index, []).append(entry_index)
        changed = False
        for index, entry_indices in matches.items():
            if len(entry_indices) != 1:
                continue
            entry_index = entry_indices[0]
            entry = sidecar[entry_index]
            copied = dict(copied_messages[index])
            copied["_source_provenance"] = {
                key: entry[key]
                for key in (
                    "request_id",
                    "source_grant_digests",
                    "content_sha256",
                    "presentation_kind",
                    "read_result_binding_id",
                )
                if key in entry
            }
            copied_messages[index] = copied
            changed = True
            consumed_entries.add(entry_index)
        if changed:
            restored["messages"] = copied_messages
        from agent.llm_egress_native import restore_native_source_sidecar
        restored["messages"] = restore_native_source_sidecar(restored["messages"], sidecar, consumed_entries)

    def _restore_input_items(input_items: Any) -> tuple[Any, bool]:
        if not isinstance(input_items, list):
            return input_items, False
        from agent.responses_source_identity import restore_responses_sources
        formatted = restore_responses_sources(input_items, sidecar, consumed_entries)
        if formatted is not None:
            return formatted, formatted != input_items
        copied_input = list(input_items)
        matches: dict[int, list[int]] = {}
        for entry_index, entry in enumerate(sidecar):
            if not isinstance(entry, Mapping) or entry_index in consumed_entries:
                continue
            expected_sha = entry.get("content_sha256")
            original_call_id = entry.get("tool_call_id")
            if not isinstance(expected_sha, str) or not isinstance(original_call_id, str):
                continue
            expected_call_id = original_call_id
            candidates: list[int] = []
            for index, item in enumerate(copied_input):
                if not isinstance(item, Mapping):
                    continue
                output = item.get("output")
                output_text = (
                    _structured_tool_output_text(output)
                    if isinstance(output, (list, Mapping))
                    else output
                )
                if (
                    item.get("type") == "function_call_output"
                    and item.get("call_id") == expected_call_id
                    and isinstance(output_text, str)
                    and sha256(output_text.encode("utf-8")).hexdigest() == expected_sha
                ):
                    candidates.append(index)
            if len(candidates) == 1:
                matches.setdefault(candidates[0], []).append(entry_index)
        changed = False
        for index, entry_indices in matches.items():
            if len(entry_indices) != 1:
                continue
            entry_index = entry_indices[0]
            entry = sidecar[entry_index]
            copied = dict(copied_input[index])
            copied["_source_provenance"] = {
                key: entry[key]
                for key in (
                    "request_id",
                    "source_grant_digests",
                    "content_sha256",
                    "presentation_kind",
                    "read_result_binding_id",
                )
                if key in entry
            }
            copied_input[index] = copied
            changed = True
            consumed_entries.add(entry_index)
        return copied_input if changed else input_items, changed

    restored_input, input_changed = _restore_input_items(restored.get("input"))
    if input_changed:
        restored["input"] = restored_input

    # The consumer-Codex SDK transform bypass moves the already-normalized
    # bulk ``input`` under ``extra_body`` immediately before dispatch.  That
    # remains provider wire data, so bind the same exact call-id/content proof
    # there as well; no other nested shape is accepted.
    extra_body = restored.get("extra_body")
    if isinstance(extra_body, Mapping):
        restored_extra_input, extra_input_changed = _restore_input_items(
            extra_body.get("input")
        )
        if extra_input_changed:
            copied_extra_body = dict(extra_body)
            copied_extra_body["input"] = restored_extra_input
            restored["extra_body"] = copied_extra_body
    return restored


def _is_codex_responses_replay_body(body: Any) -> bool:
    """Return whether *body* carries Codex Responses reasoning replay items."""

    messages = body.get("input") if isinstance(body, Mapping) else None
    if not isinstance(messages, list):
        messages = body.get("messages") if isinstance(body, Mapping) else None
    if not isinstance(messages, list):
        return False
    for message in messages:
        items: list[Any]
        if isinstance(message, Mapping) and message.get("type") == "reasoning":
            items = [message]
        elif isinstance(message, Mapping) and isinstance(message.get("content"), list):
            items = list(message["content"])
        elif isinstance(message, list):
            items = message
        else:
            continue
        for item in items:
            if (
                isinstance(item, Mapping)
                and item.get("type") == "reasoning"
                and isinstance(item.get("encrypted_content"), str)
                and isinstance(item.get("summary", []), list)
            ):
                return True
    return False


def _recognized_native_nonterminal_bindings(
    body: Mapping[str, Any],
) -> tuple[frozenset[str], frozenset[int]]:
    """Bind native result admission to exact, unique preceding registered calls.

    Return both the admitted call IDs and the identities of their direct native
    result frames.  The latter prevents a nested mapping from borrowing an ID
    merely because a valid result with that ID exists elsewhere in the body.
    """
    from agent.transports.anthropic import _unprefix_oauth_tool_name
    from tools.registry import registry as tool_registry

    def registered_name(name: Any, *, anthropic: bool = False) -> str | None:
        canonical = (
            _unprefix_oauth_tool_name(name)
            if anthropic and isinstance(name, str) and name.startswith("mcp__")
            else name
        )
        # Catalog bridges are handled outside the registry. Only these
        # read-only bridges earn admission, never tool_call.
        if (
            canonical == name
            and isinstance(name, str)
            and name.startswith("mcp__")
            and name[5:] in _REMOTE_KANBAN_TOOL_SEARCH_PROJECTION_TOOL_NAMES
        ):
            canonical = name[5:]
        if (
            not isinstance(canonical, str)
            or canonical in {"terminal", "tool_call"}
        ):
            return None
        if canonical in _REMOTE_KANBAN_TOOL_SEARCH_PROJECTION_TOOL_NAMES:
            return canonical
        return canonical if tool_registry.get_entry(canonical) is not None else None

    def scan_frames(
        frames: Any,
        *,
        call_frame: Any,
        result_frame: Any,
        anthropic: bool = False,
    ) -> tuple[set[str], set[int]]:
        if not isinstance(frames, (list, tuple)):
            return set(), set()
        calls: dict[str, str | None] = {}
        duplicates: set[str] = set()
        results: set[str] = set()
        invalid: set[str] = set()
        admitted: set[str] = set()
        admitted_result_objects: dict[str, int] = {}
        for frame in frames:
            parsed_call = call_frame(frame)
            if parsed_call is not None:
                call_id, name = parsed_call
                if not isinstance(call_id, str) or not call_id:
                    continue
                if call_id in calls:
                    duplicates.add(call_id)
                calls[call_id] = registered_name(name, anthropic=anthropic)
                continue
            parsed_result = result_frame(frame)
            if parsed_result is None:
                continue
            call_id = parsed_result
            if not isinstance(call_id, str) or not call_id:
                continue
            if call_id in results or calls.get(call_id) is None:
                invalid.add(call_id)
            results.add(call_id)
            if calls.get(call_id) is not None:
                admitted.add(call_id)
                admitted_result_objects[call_id] = id(frame)
        accepted = admitted - duplicates - invalid
        return accepted, {
            admitted_result_objects[call_id] for call_id in accepted
        }

    def anthropic_call(frame: Any) -> tuple[Any, Any] | None:
        if not isinstance(frame, Mapping) or frame.get("type") != "tool_use":
            return None
        return frame.get("id"), frame.get("name")

    def anthropic_result(frame: Any) -> Any:
        return frame.get("tool_use_id") if isinstance(frame, Mapping) and frame.get("type") == "tool_result" else None

    def chat_call(frame: Any) -> tuple[Any, Any] | None:
        if not isinstance(frame, Mapping) or frame.get("type") != "function":
            return None
        function = frame.get("function")
        if not isinstance(function, Mapping):
            return None
        return frame.get("id"), function.get("name")

    def chat_result(frame: Any) -> Any:
        return frame.get("tool_call_id") if isinstance(frame, Mapping) and frame.get("role") == "tool" else None

    def responses_call(frame: Any) -> tuple[Any, Any] | None:
        if not isinstance(frame, Mapping) or frame.get("type") != "function_call":
            return None
        return frame.get("call_id"), frame.get("name")

    def responses_result(frame: Any) -> Any:
        return frame.get("call_id") if isinstance(frame, Mapping) and frame.get("type") == "function_call_output" else None

    admitted_ids: set[str] = set()
    admitted_objects: set[int] = set()
    messages = body.get("messages", [])
    if isinstance(messages, (list, tuple)):
        # Chat Completions frames are direct messages; Anthropic frames are
        # direct content blocks. Do not recursively search nested user data.
        anthropic_shape = any(
            isinstance(message, Mapping)
            and isinstance(message.get("content"), (list, tuple))
            and any(
                isinstance(block, Mapping)
                and block.get("type") in {"tool_use", "tool_result"}
                for block in message["content"]
            )
            for message in messages
        )
        if anthropic_shape:
            frames: list[Any] = []
            for message in messages:
                if not isinstance(message, Mapping) or not isinstance(message.get("content"), (list, tuple)):
                    continue
                for block in message["content"]:
                    if isinstance(block, Mapping) and (
                        (message.get("role") == "assistant" and block.get("type") == "tool_use")
                        or (message.get("role") == "user" and block.get("type") == "tool_result")
                    ):
                        frames.append(block)
            ids, objects = scan_frames(
                frames, call_frame=anthropic_call, result_frame=anthropic_result,
                anthropic=True,
            )
            admitted_ids.update(ids)
            admitted_objects.update(objects)
        else:
            frames = []
            for message in messages:
                if not isinstance(message, Mapping):
                    continue
                if message.get("role") == "assistant":
                    calls = message.get("tool_calls")
                    if isinstance(calls, (list, tuple)):
                        frames.extend(call for call in calls if isinstance(call, Mapping))
                elif message.get("role") == "tool":
                    frames.append(message)
            ids, objects = scan_frames(frames, call_frame=chat_call, result_frame=chat_result)
            admitted_ids.update(ids)
            admitted_objects.update(objects)

    input_items = body.get("input", [])
    if isinstance(input_items, (list, tuple)):
        # Responses API has explicit top-level function_call and
        # function_call_output items. No nested/body-global shape inference.
        ids, objects = scan_frames(
            input_items, call_frame=responses_call, result_frame=responses_result
        )
        admitted_ids.update(ids)
        admitted_objects.update(objects)
    return frozenset(admitted_ids), frozenset(admitted_objects)


def authorize_agent_sdk_kwargs(
    agent: Any,
    kwargs: Mapping[str, Any],
    *,
    route: Any | None = None,
    sdk_control_keys: Sequence[str] = _SDK_CONTROL_KEYS,
) -> tuple[dict[str, Any], AuthorizedEgress]:
    controls = {key: kwargs[key] for key in sdk_control_keys if key in kwargs}
    resolved_route = _route_for_agent(agent, route)
    require_public_task_context(agent, route=resolved_route)
    route_provider = _route_field(resolved_route, "provider", "")
    protected_route = provider_uses_egress_firewall(route_provider) or owned_remote_task(agent, route=resolved_route)
    protected_remote_marker = os.environ.get("HERMES_KANBAN_PROTECTED_REMOTE") == "1"
    # Re-derive the protected Kanban boundary from durable task identity and
    # exact provider route when a reconstructed worker loses the local marker.
    # This preserves private-context handling across fallback.
    protected_kanban_remote = protected_remote_marker or (
        bool(str(os.environ.get("HERMES_KANBAN_TASK") or "").strip())
        and protected_route
    )
    sidecar = kwargs.get("_hermes_source_provenance")
    body = {
        key: value
        for key, value in kwargs.items()
        if key not in controls and key not in _INTERNAL_EGRESS_KEYS
    }
    body = _restore_source_provenance_sidecar(body, sidecar)
    classification_body = body
    if protected_kanban_remote:
        body = _sanitize_protected_kanban_body(body)
    session_id = str(getattr(agent, "session_id", "") or "")
    turn_id = str(getattr(agent, "_current_turn_id", "") or "")
    request_id = str(getattr(agent, "_current_api_request_id", "") or "")
    policy_digest = str(
        getattr(agent, "_llm_egress_policy_digest", "")
        or getattr(agent, "llm_egress_policy_digest", "")
        or DEFAULT_POLICY_DIGEST
    )
    registry = getattr(agent, "_source_provenance_registry", None)
    if protected_kanban_remote and isinstance(registry, SourceProvenanceRegistry):
        classification_body = _prepare_bound_skill_source_results(
            classification_body, registry,
            (session_id, turn_id, request_id, policy_digest),
        )
        body = _sanitize_protected_kanban_body(classification_body)
    grants = (
        registry.grants_for_request(request_id)
        if isinstance(registry, SourceProvenanceRegistry)
        else ()
    )
    sanitized_segment_cap = int(
        getattr(agent, "_llm_egress_max_sanitized_segment_bytes", 32_768)
    )
    sanitized_aggregate_cap = int(
        # The per-segment cap (32,768) already bounds any single chunked
        # piece; the aggregate exists to bound how many such chunks one
        # request may carry in total, so it must be a real multiple of the
        # segment cap or chunking (which exists specifically to split
        # oversized-but-legitimate tool content, e.g. a read_file result,
        # into sub-cap pieces) is defeated by anything over one chunk.
        # Bounded by the same ceiling already enforced on the request as a
        # whole (max_serialized_bytes' default), never a smaller ad hoc
        # number.
        getattr(
            agent,
            "_llm_egress_max_sanitized_bytes",
            32_768 if getattr(agent, "_llm_egress_aux_task", "") == "vision" else 262_144,
        )
    )
    used_grants = SourceReadSelection()
    # Protected providers always use bounded context. The local marker still
    # controls path redaction and the reduced Kanban toolset, but reconstructed
    # workers may lose it. Route-based framing prevents large requests from
    # raising during typing, bypassing receipts and triggering fallback loops.
    protected_remote_context = protected_remote_marker or protected_route
    # Only application-owned framing gets path/Base64 redaction; user content
    # and unbound tool results remain fail-closed.
    redact_protected_generated_context = (
        str(route_provider or "").strip().lower() == "openai-codex"
        or protected_route
    )
    native_nonterminal_call_ids, native_nonterminal_result_ids = (
        _recognized_native_nonterminal_bindings(body)
        if protected_route and not protected_kanban_remote
        else (frozenset(), frozenset())
    )
    typed_body = _typed_payload(
        body,
        _grant_texts(grants),
        used_grants,
        sanitized_cap=sanitized_segment_cap,
        syntax_tool_call_ids=(
            _recognized_syntax_tool_call_ids(body)
            if protected_kanban_remote or protected_route
            else frozenset()
        ),
        pytest_terminal_call_ids=(
            _pytest_terminal_call_ids(body)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        elided_kanban_tool_call_ids=(
            _recognized_tool_call_ids(body, _REMOTE_KANBAN_PROJECTION_TOOL_NAMES)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        kanban_attachment_tool_call_ids=(
            _recognized_tool_call_ids(body, _REMOTE_KANBAN_ATTACHMENT_TOOL_NAMES)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        kanban_lifecycle_tool_call_ids=(
            _recognized_tool_call_ids(body, _REMOTE_KANBAN_LIFECYCLE_TOOL_NAMES)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        search_projection_tool_call_ids=(
            _recognized_tool_call_ids(
                body, _REMOTE_KANBAN_SEARCH_PROJECTION_TOOL_NAMES
            )
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        tool_search_projection_tool_call_ids=(
            _recognized_tool_call_ids(
                body, _REMOTE_KANBAN_TOOL_SEARCH_PROJECTION_TOOL_NAMES
            )
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        read_file_projection_tool_call_ids=(
            _recognized_tool_call_ids(
                body, _REMOTE_KANBAN_READ_FILE_PROJECTION_TOOL_NAMES
            )
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        web_replay_tool_call_ids=(
            _recognized_tool_call_ids(body, _REMOTE_KANBAN_WEB_REPLAY_TOOL_NAMES)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        file_mutation_replay_tool_call_ids=(
            _recognized_tool_call_ids(
                body, _REMOTE_KANBAN_FILE_MUTATION_REPLAY_TOOL_NAMES
            )
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        scratch_read_file_tool_call_ids=(
            _scratch_read_file_tool_call_ids(classification_body)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        git_workspace_diagnostic_call_ids=(
            _git_workspace_diagnostic_call_ids(body)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        git_grep_projection_tool_call_ids=(
            _git_grep_terminal_call_ids(body)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        rg_projection_tool_call_ids=(
            _rg_terminal_call_ids(body)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        git_diff_name_only_projection_tool_call_ids=(
            _git_diff_name_only_terminal_call_ids(body)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        git_review_summary_projection_tool_call_ids=(
            _git_review_summary_terminal_call_ids(body)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        github_pr_feedback_terminal_call_ids=(
            _github_pr_feedback_terminal_call_ids(body)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        kanban_assignees_terminal_call_ids=(
            _kanban_assignees_terminal_call_ids(body)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        github_list_terminal_call_limits=(
            _github_list_terminal_call_limits(body)
            if protected_kanban_remote and protected_route
            else None
        ),
        github_api_extract_call_limits=(
            _github_api_extract_call_limits(body)
            if protected_kanban_remote and protected_route
            else None
        ),
        github_api_paginate_call_limits=(
            _github_api_paginate_terminal_call_limits(body)
            if protected_kanban_remote and protected_route
            else None
        ),
        github_api_curl_terminal_call_ids=(
            _github_api_curl_terminal_call_ids(body)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        plain_github_list_terminal_call_ids=(
            _plain_github_list_terminal_call_ids(body)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        combined_github_list_terminal_call_limits=(
            _combined_github_list_terminal_call_limits(body)
            if protected_kanban_remote and protected_route
            else None
        ),
        combined_github_view_terminal_call_limits=(
            _combined_github_view_terminal_call_limits(body)
            if protected_kanban_remote and protected_route
            else None
        ),
        rejected_terminal_call_ids=(
            _rejected_terminal_call_ids(body)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        terminal_replay_tool_call_ids=(
            _recognized_tool_call_ids(body, _REMOTE_KANBAN_TERMINAL_REPLAY_TOOL_NAMES)
            if protected_kanban_remote and protected_route
            else frozenset()
        ),
        redact_readonly_tool_arguments=(
            protected_kanban_remote and protected_route
        ),
        redact_terminal_arguments=(
            protected_kanban_remote and protected_route
        ),
        native_nonterminal_call_ids=native_nonterminal_call_ids,
        native_nonterminal_result_ids=native_nonterminal_result_ids,
        require_terminal_provenance=(
            protected_route and not protected_kanban_remote
        ),
        protected_kanban_context=protected_remote_context,
        redact_generated_context=redact_protected_generated_context,
        allow_codex_reasoning_replay=(
            str(route_provider or "").strip().lower() == "openai-codex"
            and (
                str(getattr(agent, "api_mode", "") or "") == "codex_responses"
                or _is_codex_responses_replay_body(body)
            )
        ),
        # Anthropic's Messages API returns a `signature` on each `thinking`
        # block, and Hermes must replay it byte-exact on a later turn (the
        # API rejects a request whose prior thinking block was altered).
        # Only the Anthropic route needs this opaque-replay exemption, same
        # as the Codex reasoning replay above being scoped to that route.
        allow_anthropic_thinking_replay=(
            str(route_provider or "").strip().lower() == "anthropic"
        ),
        registry=registry if isinstance(registry, SourceProvenanceRegistry) else None,
        request_identity=(session_id, turn_id, request_id, policy_digest),
    )
    request = TypedOutboundRequest(
        payload=type_builtin_schema_words(body, type_tool_diagnostics(agent, body, project_cron_list(body, typed_body, protected_kanban_remote), protected_kanban_remote)),
        session_id=session_id,
        turn_id=turn_id,
        request_id=request_id,
        policy_digest=policy_digest,
    )
    from hermes_constants import get_hermes_home

    _configured_state_dir = getattr(agent, "_llm_egress_state_dir", "")
    state_dir = Path(_configured_state_dir) if _configured_state_dir else (
        Path(get_hermes_home()) / "egress"
    )
    max_serialized_bytes = int(
        getattr(agent, "_llm_egress_max_serialized_bytes", 262_144)
    )
    max_conservative_tokens = int(
        getattr(agent, "_llm_egress_max_conservative_tokens", 87_382)
    )
    firewall = LLMEgressFirewall(
        state_dir,
        policy_digest=policy_digest,
        max_serialized_bytes=max_serialized_bytes,
        max_conservative_tokens=max_conservative_tokens,
        max_granted_serialized_bytes=int(
            getattr(
                agent,
                "_llm_egress_max_granted_serialized_bytes",
                max_serialized_bytes,
            )
        ),
        max_granted_conservative_tokens=int(
            getattr(
                agent,
                "_llm_egress_max_granted_conservative_tokens",
                max_conservative_tokens,
            )
        ),
        max_sanitized_bytes=sanitized_aggregate_cap,
        max_sanitized_segment_bytes=sanitized_segment_cap,
        static_literal_hashes_by_policy={
            policy_digest: _structural_literal_hashes(body)
        },
        exact_secret_values=_exact_provider_secret_values(),
    )
    try:
        from agent.source_provenance_diagnostics import receipt_scope
        with receipt_scope(agent, registry, (session_id, turn_id, request_id, policy_digest)):
            authorization = firewall.authorize(
                request,
                resolved_route,
                grants=tuple(used_grants.values()),
            )
    except EgressBlocked:
        logger.warning(
            "LLM egress blocked grant counts: input=%d selected=%d",
            len(grants), len(used_grants),
        )
        typed_locations = _typed_payload_violation_locations(typed_body)
        if typed_locations:
            logger.warning(
                "LLM egress blocked typed locations: %s", typed_locations
            )
        locations = content_free_violation_locations(body)
        if locations:
            logger.warning("LLM egress blocked structural locations: %s", locations)
        raise
    remember_validated_sources(registry, used_grants)
    rebuilt = json.loads(authorization.payload_bytes)
    if not isinstance(rebuilt, dict):
        raise TypeError("authorized provider payload must be a JSON object")
    rebuilt.update(controls)
    return rebuilt, authorization


def dispatch_authorized_agent_request(
    agent: Any,
    kwargs: Mapping[str, Any],
    callback: Callable[[dict[str, Any]], Any],
    *,
    route: Any | None = None,
    sdk_control_keys: Sequence[str] = _SDK_CONTROL_KEYS,
) -> Any:
    resolved_route = _route_for_agent(agent, route)
    mark_local_task_context(agent, route=resolved_route)
    if not egress_enforcement_enabled():
        return callback({key: value for key, value in kwargs.items() if key not in _INTERNAL_EGRESS_KEYS})
    destination = classify_destination(
        str(_route_field(resolved_route, "provider", "") or ""),
        _route_field(resolved_route, "base_url"),
        _route_field(resolved_route, "api_mode"),
    )
    if destination in {DestinationClass.LOCAL_PROCESS, DestinationClass.LOOPBACK}:
        return callback({key: value for key, value in kwargs.items() if key not in _INTERNAL_EGRESS_KEYS})
    authorized, receipt = authorize_agent_sdk_kwargs(
        agent,
        kwargs,
        route=resolved_route,
        sdk_control_keys=sdk_control_keys,
    )
    # Recreate the exact body digest immediately before the provider callback.
    # Only explicit non-content SDK controls are excluded; headers/query are
    # scanned and included in the firewall-authorized JSON body.
    wire_body = {
        key: value for key, value in authorized.items() if key not in sdk_control_keys
    }
    wire_bytes = json.dumps(
        wire_body,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    receipt.verify_payload(wire_bytes)
    return callback(MappingProxyType(authorized))
