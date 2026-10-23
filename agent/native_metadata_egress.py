"""Request-local references to observed, independently derived native ID leaves.

No serialized proof, source grant or tool permission is issued. References exist
only while the actual firewall authorizes one exact owner/request/recipient.
External content and all secret/path checks remain independent.
"""
from collections import Counter
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from hashlib import sha256
import json


_ACTIVE = ContextVar("native_metadata_admission", default=None)


@dataclass(frozen=True)
class NativeMetadataSegment:
    text: str
    reference: object


def recipient_digest(route):
    def field(name):
        value = route.get(name) if isinstance(route, Mapping) else getattr(route, name, None)
        return value if isinstance(value, str) else None
    return sha256(json.dumps([field(key) for key in (
        "provider", "model", "base_url", "api_mode")], separators=(",", ":")).encode()).hexdigest()


def _identity(agent):
    return tuple(str(getattr(agent, key, "") or "") for key in (
        "session_id", "_current_turn_id", "_current_api_request_id", "_llm_egress_policy_digest"))


def validate_segment(segment, request):
    """A caller-constructed dataclass or stale reference conveys no authority."""
    active = _ACTIVE.get()
    identity = (request.session_id, request.turn_id, request.request_id, request.policy_digest)
    if active is None or active[0] != identity:
        raise ValueError("no current native metadata admission")
    item = active[1].get(segment.reference)
    if item is None or segment.text != item[0]:
        raise ValueError("native metadata reference mismatch")
    agent, owner, route, recipient = active[2:]
    from agent.native_metadata_capture import _current_owner, _owner_store, _LOCK, capture_key
    from tools.registry import registry
    record = item[2]
    if (_identity(agent) != identity or _current_owner(agent) is not owner
            or recipient_digest(route) != recipient
            or registry.get_entry(record.tool_name) is not record.entry):
        raise ValueError("native metadata owner or recipient changed")
    with _LOCK:
        if _owner_store(agent, owner)[1].get(capture_key(record)) is not record:
            raise ValueError("native metadata capture invalidated")
    return item[1]


def _presentation_raw(record, content, limit):
    from agent.tool_dispatch_helpers import _maybe_wrap_untrusted

    if len(content) > limit or sha256(content.encode()).hexdigest() != record.presentation_sha256:
        return None
    # Invert ONLY the exact existing application-owned wrapper. Its fixed prefix
    # and suffix are equality checked by the same producer, not matched as tags.
    if sha256(content.encode()).hexdigest() == record.result_sha256:
        return content
    marker = "native-metadata-boundary-placeholder"
    wrapped_marker = _maybe_wrap_untrusted(record.tool_name, marker)
    if wrapped_marker == marker:
        raw = content
    else:
        prefix, suffix = wrapped_marker.split(marker)
        if not content.startswith(prefix) or not content.endswith(suffix):
            return None
        raw = content[len(prefix):len(content) - len(suffix)]
        if _maybe_wrap_untrusted(record.tool_name, raw) != content:
            return None
    if sha256(raw.encode()).hexdigest() != record.result_sha256:
        return None
    return raw


def _presentation_projection(record, content, limit):
    from agent.native_metadata_derivation import verify_metadata_presentation
    raw = _presentation_raw(record, content, limit)
    if raw is None:
        return None
    if record.tool_name.endswith("__a2a_ack"):
        # No returned identifier exists. Preserve ordinary body scanning while
        # accounting for the exact same application-owned wrapper only.
        projected = raw
        return _wrapper_projection(record.tool_name, content, raw, projected)
    spans = verify_metadata_presentation(operation=record.tool_name.rsplit("__", 1)[-1], expected=record.expected,
        text=raw, presentation_sha256=record.result_sha256, max_bytes=limit)
    if len(spans) != 2:
        return None
    encoded, pieces, position = raw.encode(), [], 0
    for span in spans:
        pieces.append(encoded[position:span.start])
        pieces.append(b" " * (span.end - span.start))  # Projection only; wire bytes stay exact.
        position = span.end
    pieces.append(encoded[position:])
    return _wrapper_projection(record.tool_name, content, raw, b"".join(pieces).decode())


def _wrapper_projection(name, content, raw, projected):
    if content == raw:
        return projected
    from agent.tool_dispatch_helpers import _maybe_wrap_untrusted
    marker = "native-metadata-boundary-placeholder"
    prefix, suffix = _maybe_wrap_untrusted(name, marker).split(marker)
    return " " * len(prefix) + projected + " " * len(suffix)


def _bind_reference(text, projection, record, references, cap):
    from agent.llm_egress_firewall import OutboundText
    from agent.llm_egress_terminal import _split_utf8_chunks
    if len(text) != len(projection) or cap <= 0:
        return None
    parts, position = [], 0
    for chunk in _split_utf8_chunks(text, cap):
        ref = object()
        references[ref] = (chunk, projection[position:position + len(chunk)], record)
        parts.append(NativeMetadataSegment(chunk, ref))
        position += len(chunk)
    return OutboundText(tuple(parts))


def _sequence(value):
    return value if isinstance(value, (list, tuple)) else ()


def _input(body):
    extra = body.get("extra_body")
    return _sequence(extra["input"] if isinstance(extra, Mapping) and "input" in extra else body.get("input"))


@dataclass(frozen=True)
class _CallFrame:
    call_id: object
    name: object
    argument_sha256: object
    name_container: object
    argument_container: object
    argument_field: str | None


def _call_frames(body):
    from agent.native_metadata_capture import argument_digest, _unique_object
    from agent.transports.anthropic import _unprefix_oauth_tool_name

    def arguments(value):
        try:
            return argument_digest(json.loads(value, object_pairs_hook=_unique_object)) if isinstance(value, str) else argument_digest(value)
        except (ValueError, TypeError):
            return None
    for frame in _sequence(body.get("messages")):
        if not isinstance(frame, dict) or frame.get("role") != "assistant":
            continue
        for call in _sequence(frame.get("tool_calls")):
            if isinstance(call, dict) and isinstance(call.get("function"), dict):
                function = call["function"]
                yield _CallFrame(call.get("id"), function.get("name"), arguments(function.get("arguments")), function, function, "arguments")
        for call in _sequence(frame.get("content")):
            if isinstance(call, dict) and call.get("type") == "tool_use":
                name = call.get("name")
                if isinstance(name, str) and name.startswith("mcp__"):
                    name = _unprefix_oauth_tool_name(name)
                yield _CallFrame(call.get("id"), name, arguments(call.get("input")), call, call.get("input"), None)
    for call in _input(body):
        if isinstance(call, dict) and call.get("type") == "function_call":
            yield _CallFrame(call.get("call_id"), call.get("name"), arguments(call.get("arguments")), call, call, "arguments")


def _argument_locations(call, record):
    if record.tool_name.rsplit("__", 1)[-1] not in {"a2a_read", "a2a_ack"}:
        return
    if call.argument_field is not None:
        yield call.argument_container, call.argument_field
    elif isinstance(call.argument_container, Mapping):
        for field in ("request_id", "event_id"):
            if field in call.argument_container:
                yield call.argument_container, field


def _frame_indexes(body, admitted_calls, admitted_results):
    calls = tuple(_call_frames(body))
    counts = Counter(call.call_id for call in calls if isinstance(call.call_id, str))
    indexed_calls = {call.call_id: call for call in calls
        if isinstance(call.call_id, str) and counts[call.call_id] == 1
        and isinstance(call.name, str) and isinstance(call.argument_sha256, str)}
    results = {}
    duplicates = set()
    for frame, field, call_id in _results(body):
        if not isinstance(call_id, str) or call_id not in admitted_calls or id(frame) not in admitted_results:
            continue
        if call_id in results:
            duplicates.add(call_id)
        results[call_id] = (frame, field)
    return indexed_calls, {key: value for key, value in results.items() if key not in duplicates}


def _arguments_projection(record, field, text):
    from agent.native_metadata_derivation import _object_fields
    if field in {"request_id", "event_id"}:
        return " " * len(text) if text == getattr(record.expected, field) else None
    try:
        value, tokens = _object_fields(text)
        from agent.native_metadata_capture import argument_digest
        if argument_digest(value) != record.argument_sha256:
            return None
        spans = []
        for name in ("request_id", "event_id"):
            if name not in value:
                continue
            expected = getattr(record.expected, name)
            start, end = tokens[name]
            if value[name] != expected or text[start:end] != '"' + expected + '"':
                return None
            spans.append((start + 1, end - 1))
        pieces, position = [], 0
        for start, end in sorted(spans):
            pieces.extend((text[position:start], " " * (end - start)))
            position = end
        pieces.append(text[position:])
        return "".join(pieces) if spans else None
    except (ValueError, TypeError, KeyError, IndexError):
        return None


# Exact existing native wake producer framing; matching it authenticates no event.
_COMPLETION_CONTEXT = (
    "A2A terminal event. Do not submit another consultation. "
    "Read this exact request with a2a_read, check source/result identity, "
    "acknowledge the returned event_id and reply_sha256 with a2a_ack, and summarize. Event: ")


def _contexts(body):
    for frame in (*_sequence(body.get("messages")), *_input(body)):
        if not isinstance(frame, dict) or frame.get("role") != "user":
            continue
        content = frame.get("content")
        if isinstance(content, str):
            yield frame, "content"
        for part in _sequence(content):
            if isinstance(part, dict) and part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str):
                yield part, "text"


def _context_request_id(text, limit):
    from agent.native_metadata_derivation import _object_fields
    if not text.startswith(_COMPLETION_CONTEXT) or len(text) > limit:
        return None
    try:
        value, _fields = _object_fields(text[len(_COMPLETION_CONTEXT):])
        return value.get("request_id")
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        return None


def _known_context_projection(record, text, limit):
    """Reuse two previously admitted bytes; authenticate no message or operation."""
    from agent.native_metadata_derivation import _object_fields
    if not text.startswith(_COMPLETION_CONTEXT) or len(text) > limit:
        return None
    raw = text[len(_COMPLETION_CONTEXT):]
    try:
        value, fields = _object_fields(raw)
        known = {"request_id": record.request_id, "completion_event_id": record.event_id}
        spans = []
        for field, expected in known.items():
            if value.get(field) != expected:
                return None
            start, end = fields[field]
            if raw[start:end] != '"' + expected + '"':
                return None
            spans.append((start + 1, end - 1))
        pieces, position = [_COMPLETION_CONTEXT], 0
        for start, end in sorted(spans):
            pieces.extend((raw[position:start], " " * (end - start)))
            position = end
        pieces.append(raw[position:])
        return "".join(pieces)
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        return None


def _validated_store(owner):
    live = owner.authority.sessions[owner.logical_session_id]
    store = getattr(live, "_native_metadata_admitted", None)
    if store is None or store[0] != owner.key:
        store = (owner.key, {})
        live._native_metadata_admitted = store
    return store[1]


def _results(body):
    """Direct protocol result frames only; pairing is independently supplied."""
    for frame in _sequence(body.get("messages")):
        if not isinstance(frame, dict):
            continue
        if frame.get("role") == "tool":
            yield frame, "content", frame.get("tool_call_id")
        if frame.get("role") == "user" and isinstance(frame.get("content"), list):
            for part in frame["content"]:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    yield part, "content", part.get("tool_use_id")
    for frame in _input(body):
        if isinstance(frame, dict) and frame.get("type") == "function_call_output":
            yield frame, "output", frame.get("call_id")


@contextmanager
def admission(agent, route, body, typed, admitted_calls, admitted_results, *, enabled=True):
    """Bind private leaves to this exact effective SDK body and actual route."""
    from agent.native_metadata_capture import _current_owner, _owner_store, _LOCK, metadata_byte_limit
    from tools.registry import registry

    limit = metadata_byte_limit(agent)
    segment_cap = int(getattr(agent, "_llm_egress_max_sanitized_segment_bytes", 32768))
    identity, recipient = _identity(agent), recipient_digest(route)
    references, replacements, selected = {}, {}, []
    owner = _current_owner(agent) if enabled else None
    calls, results = _frame_indexes(body, admitted_calls, admitted_results) if owner is not None else ({}, {})
    if owner is not None and all(identity):
        with _LOCK:
            captures = dict(_owner_store(agent, owner)[1])
            records = tuple(captures.values())
            validated_records = tuple(_validated_store(owner).values())
        admitted_record_ids = {id(record) for record in validated_records}
        for record in records:
            call = calls.get(record.call_id) if record is not None else None
            result = results.get(record.call_id) if call is not None else None
            if (call is None or result is None or call.name != record.tool_name
                    or call.argument_sha256 != record.argument_sha256
                    or record.owner_key != owner.key or record.expected is None
                    or record.recipient_digest != recipient or record.policy_digest != identity[3]
                    or (id(record) not in admitted_record_ids and
                        (record.turn_id != identity[1] or record.next_provider_request_id != identity[2]))
                    or not record.presentation_sha256 or registry.get_entry(record.tool_name) is not record.entry):
                continue
            frame, field = result
            if (not isinstance(frame.get(field), str) or frame.get("is_error") or frame.get("blocked")
                    or frame.get("status", "completed") != "completed"
                    or _presentation_raw(record, frame[field], limit) is None):
                continue
            text = call.name_container["name"]
            replacements[(id(call.name_container), "name")] = _bind_reference(
                text, " " * len(text), record, references, segment_cap)
            for container, argument_field in _argument_locations(call, record):
                text = container.get(argument_field)
                if not isinstance(text, str):
                    continue
                projection = _arguments_projection(record, argument_field, text)
                if projection is not None:
                    replacements[(id(container), argument_field)] = _bind_reference(
                        text, projection, record, references, segment_cap)
            projection = _presentation_projection(record, frame[field], limit)
            if projection is not None:
                replacements[(id(frame), field)] = _bind_reference(
                    frame[field], projection, record, references, segment_cap)
                selected.append(record)

        from agent.native_metadata_capture import capture_key
        known = {record.request_id: record for record in validated_records
            if record.owner_key == owner.key and record.policy_digest == identity[3]
            and record.recipient_digest == recipient and captures.get(capture_key(record)) is record
            and registry.get_entry(record.tool_name) is record.entry}
        for container, field in _contexts(body):
            text = container[field]
            request_id = _context_request_id(text, limit)
            record = known.get(request_id) if isinstance(request_id, str) else None
            projection = _known_context_projection(record, text, limit) if record is not None else None
            if projection is not None:
                replacements[(id(container), field)] = _bind_reference(
                    text, projection, record, references, segment_cap)

    replacements = {key: value for key, value in replacements.items() if value is not None}

    def project(original, classified):
        if isinstance(original, dict) and isinstance(classified, dict):
            return {key: replacements.get((id(original), getattr(key, "text", key)),
                project(original.get(getattr(key, "text", key)), value))
                for key, value in classified.items()}
        if isinstance(original, (list, tuple)) and isinstance(classified, (list, tuple)):
            return [project(a, b) for a, b in zip(original, classified)] if len(original) == len(classified) else classified
        return classified

    token = _ACTIVE.set((identity, references, agent, owner, route, recipient))
    try:
        def accepted():
            # Called only after the actual firewall succeeds. No denied request
            # can bootstrap later read/ACK or context identifier reuse.
            if owner is not None and _current_owner(agent) is owner and _identity(agent) == identity:
                with _LOCK:
                    store = _validated_store(owner)
                    for record in selected:
                        key = (record.turn_id, record.execution_api_request_id,
                               record.call_id, record.presentation_sha256)
                        store[key] = record
        yield project(body, typed), accepted
    finally:
        _ACTIVE.reset(token)


def remember_authorized_recipient(agent, route):
    """Only successful real authorization can bind the preceding execution route."""
    try:
        agent._native_metadata_authorized_recipient = (_identity(agent), recipient_digest(route))
    except Exception:  # health: allow BLE001 -- Optional observation must not alter authorization or log private exception data.
        return


def authorize_with_native_metadata(agent, route, body, request, calls, results, firewall, grants, *, enabled):
    with admission(agent, route, body, request.payload, calls, results, enabled=enabled) as (payload, accepted):
        authorization = firewall.authorize(replace(request, payload=payload), route, grants=grants)
        accepted()
        return authorization


def _render_bounded_sanitized(segment, request, grants, allowed, total, segment_cap, aggregate_cap):
    """Common original sanitizer budgets/scans, with request-bound metadata projection."""
    from agent.llm_egress_firewall import _contains_grant_substring

    reasons = []
    if not allowed:
        reasons.append("sanitized_segment_forbidden")
    if not isinstance(segment.text, str):
        return "", "", total, [*reasons, "invalid_literal_segment"]
    projection = segment.text
    if isinstance(segment, NativeMetadataSegment):
        from agent.native_metadata_egress import validate_segment
        try:
            projection = validate_segment(segment, request)
        except (ValueError, TypeError):
            return "", "", total, [*reasons, "invalid_native_metadata_reference"]
    encoded = segment.text.encode("utf-8")
    if len(encoded) > segment_cap:
        reasons.append("sanitized_segment_bytes_exceeded")
    total += len(encoded)
    if total > aggregate_cap:
        reasons.append("sanitized_bytes_exceeded")
    if any(_contains_grant_substring(content, encoded) for _, content in grants.values()):
        reasons.append("source_bytes_in_sanitized_segment")
    return segment.text, projection, total, reasons
