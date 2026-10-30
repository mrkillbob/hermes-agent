"""Pure native correlation derivation and byte positions, never egress authority.

Inputs must come from the caller's independently captured owner/call facts. These
public data values do not authenticate a capture, reply, successful operation or
provider request. Consumers must separately bind owner, call and presentation.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import re
import uuid


_JSON_WHITESPACE = " \t\r\n"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ExpectedIdentifiers:
    request_id: str
    event_id: str
    target: str
    logical_session_id: str
    work_item_id: str


@dataclass(frozen=True)
class DerivedValueSpan:
    """Whole identifier contents, excluding JSON quotes, in original UTF-8 bytes."""
    kind: str
    start: int
    end: int
    value: bytes
    presentation_sha256: str


def expected_submit_ids(*, logical_session_id, target, work_item_id):
    """Recompute the exact native recipe; no response supplies derivation inputs."""
    if (type(logical_session_id) is not str or not logical_session_id
            or type(work_item_id) is not str or not work_item_id
            or type(target) is not str or target not in {"claude", "codex"}):
        return None
    try:
        # Native caller_tools.canonical deliberately uses default ensure_ascii=True.
        logical_session_id.encode("utf-8")
        work_item_id.encode("utf-8")
        canonical = json.dumps(["hermes-" + logical_session_id, "hermes", target, work_item_id],
                               sort_keys=True, separators=(",", ":"), allow_nan=False)
        request = str(uuid.uuid5(uuid.NAMESPACE_URL, canonical))
        event = "evt_" + uuid.uuid5(uuid.NAMESPACE_URL, "mira-claude:" + request).hex
        return ExpectedIdentifiers(request, event, target, logical_session_id, work_item_id)
    except (ValueError, UnicodeError):
        return None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-JSON numeric constant")


_DECODER = json.JSONDecoder(object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def _object_fields(text):
    """Decode a full object and locate only its immediate value tokens."""
    start = len(text) - len(text.lstrip(_JSON_WHITESPACE))
    value, end = _DECODER.raw_decode(text, start)
    if type(value) is not dict or text[end:].strip(_JSON_WHITESPACE):
        raise ValueError("one complete JSON object required")
    fields, position = {}, start + 1
    while position < end:
        while text[position] in _JSON_WHITESPACE:
            position += 1
        if text[position] == "}":
            break
        key, position = _DECODER.raw_decode(text, position)
        while text[position] in _JSON_WHITESPACE:
            position += 1
        if text[position] != ":":
            raise ValueError("JSON field boundary required")
        position += 1
        while text[position] in _JSON_WHITESPACE:
            position += 1
        token_start = position
        _item, position = _DECODER.raw_decode(text, position)
        fields[key] = (token_start, position)
        while text[position] in _JSON_WHITESPACE:
            position += 1
        if text[position] == "}":
            break
        if text[position] != ",":
            raise ValueError("JSON field boundary required")
        position += 1
    return value, fields


def _string_positions(text, start, end):
    """Map decoded string characters to original character intervals, without rewriting."""
    if text[start] != '"' or text[end - 1] != '"':
        raise ValueError("JSON string required")
    decoded, positions, cursor = [], [], start + 1
    while cursor < end - 1:
        width = 1
        if text[cursor] == "\\":
            width = 6 if text[cursor + 1] == "u" else 2
            if width == 6 and 0xD800 <= int(text[cursor + 2:cursor + 6], 16) <= 0xDBFF:
                width = 12  # Validated JSON may carry a UTF-16 surrogate pair.
            piece = json.loads('"' + text[cursor:cursor + width] + '"')
        else:
            piece = text[cursor]
        if len(piece) != 1:
            raise ValueError("unambiguous JSON character required")
        decoded.append(piece)
        positions.append((cursor, cursor + width))
        cursor += width
    if cursor != end - 1:
        raise ValueError("complete JSON string required")
    return "".join(decoded), positions


def _payload_spans(operation, expected, text, digest):
    outer, fields = _object_fields(text)
    if set(outer) != {"result"} or type(outer["result"]) is not str:
        return ()
    inner, positions = _string_positions(text, *fields["result"])
    if inner != outer["result"]:
        return ()
    inner.encode("utf-8")
    payload, tokens = _object_fields(inner)
    if (operation not in {"a2a_submit", "a2a_read"} or "error" in payload
            or payload.get("target", expected.target) != expected.target
            or payload.get("origin", "hermes") != "hermes"
            or (operation == "a2a_submit" and "reply" in payload)
            or (operation == "a2a_read" and type(payload.get("reply")) is not str)):
        return ()
    spans = []
    for kind in ("request_id", "event_id"):
        value = getattr(expected, kind)
        if payload.get(kind) != value:
            return ()
        start, end = tokens[kind]
        # Escaped identifiers are not the exact expected bytes. Do not normalize
        # or grant a larger enclosing field/string to accommodate their spelling.
        if inner[start:end] != '"' + value + '"':
            return ()
        first, last = positions[start + 1][0], positions[end - 2][1]
        begin = len(text[:first].encode("utf-8"))
        finish = len(text[:last].encode("utf-8"))
        raw = text.encode("utf-8")[begin:finish]
        if raw != value.encode("ascii"):
            return ()
        spans.append(DerivedValueSpan(kind, begin, finish, raw, digest))
    return tuple(sorted(spans, key=lambda span: span.start))


def verify_metadata_presentation(*, operation, expected, text, presentation_sha256, max_bytes=None):
    """Locate exact derived values in a captured MCP presentation, or decline.

    Only the existing outer {result: JSON-string} presentation is supported.
    Unknown fields, hashes, state and external reply receive no spans. ACK emits
    a boolean and therefore has no identifier-value span. Owner/call authenticity,
    successful capture and prior-submit ownership for read/ACK are issuer duties.
    """
    if (type(expected) is not ExpectedIdentifiers or type(text) is not str
            or (max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0 or len(text) > max_bytes))
            or type(presentation_sha256) is not str or not _DIGEST.fullmatch(presentation_sha256)):
        return ()
    try:
        derived = expected_submit_ids(logical_session_id=expected.logical_session_id,
                                      target=expected.target, work_item_id=expected.work_item_id)
        if derived != expected:
            return ()
        raw = text.encode("utf-8")
        if (max_bytes is not None and len(raw) > max_bytes) or sha256(raw).hexdigest() != presentation_sha256:
            return ()
        return _payload_spans(operation, expected, text, presentation_sha256)
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError):
        return ()
