"""Pure native ID recipes and exact presentation spans convey no admission authority."""

from dataclasses import replace
from hashlib import sha256
import importlib
import importlib.util
import json

import pytest


GOLDENS = [
    ("local-session", "codex", "item-1", "3c856b95-15ce-5f0d-926e-d0d0b72c9e7f", "evt_ff0679cca4185f19bde398b62070b29c"),
    ("local-session", "claude", "item-1", "6f70d2d0-7824-54a3-a9e5-6870fecc895e", "evt_2f1f3831907e5491825ec5ac5ca997ac"),
    ("会話", "codex", "réparer-雪", "3a392377-b276-5b34-9edc-89e60adec08a", "evt_955159d3650c58219653311f740bc815"),
]


def _api():
    assert importlib.util.find_spec("agent.native_metadata_derivation") is not None, "pure derivation interface absent"
    return importlib.import_module("agent.native_metadata_derivation")


@pytest.mark.parametrize("values", GOLDENS + [
    ("", "codex", "item", None, None), ("session", "hermes", "item", None, None),
    ("session", "mira", "item", None, None), ("session", "codex", "", None, None),
    (False, "codex", "item", None, None), ("session", "codex", None, None, None),
    ("session", "codex", "\ud800", None, None),
])
def test_identifiers_follow_native_ascii_canonical_recipe_only(values):
    api = _api()
    logical, target, work, request, event = values
    result = api.expected_submit_ids(logical_session_id=logical, target=target, work_item_id=work)
    if request is None:
        assert result is None
    else:
        assert (result.request_id, result.event_id) == (request, event)
        assert result.event_id.startswith("evt_") and not result.event_id.startswith("terminal_")


def _presentation(case, expected):
    payload = {"request_id": expected.request_id, "event_id": expected.event_id,
               "origin": "hermes", "target": "codex", "state": "awaiting_ack"}
    operation, digest_change, ensure_ascii = "a2a_submit", False, True
    if case == "read":
        operation = "a2a_read"
        payload["reply"] = "external harmless text"
    updates = {
        "wrong-request": {"request_id": GOLDENS[1][3]},
        "wrong-event": {"event_id": "terminal_" + "1" * 32},
        "wrong-target": {"target": "claude"}, "wrong-origin": {"origin": "codex"},
        "unknown-content": {"extra": {"request_id": expected.request_id,
            "reply": "c2VjcmV0LXBheWxvYWQ= token=synthetic-secret-value"}},
        "unicode": {"annotation": "雪 😀"},
        "unicode-escaped": {"annotation": "雪 😀"},
        "oversize": {"unknown": "x" * 65536},
        "boolean-id": {"request_id": True},
    }
    payload.update(updates.get(case, {}))
    if case == "nested":
        payload = {"nested": payload}
    if case == "wrong-key":
        payload["copy_request_id"] = payload.pop("request_id")
    if case == "ack":
        operation, payload = "a2a_ack", {"acknowledged": True}
    if case == "unknown-operation":
        operation = "a2a_policy"
    if case == "read-without-reply":
        operation = "a2a_read"
    if case == "submit-with-reply":
        payload["reply"] = "external text"
    inner = json.dumps(payload, sort_keys=True, ensure_ascii=case not in {"unicode", "unicode-escaped"}, separators=(",", ":"))
    if case in {"duplicate", "duplicate-escaped-key"}:
        key = "request_id" if case == "duplicate" else "request_\\u0069d"
        inner = inner[:-1] + ',"' + key + '":"' + expected.request_id + '"}'
    if case == "nested-duplicate":
        inner = inner[:-1] + ',"extra":{"same":1,"same":2}}'
    if case == "escaped-value":
        inner = inner.replace(expected.request_id, "\\u0033" + expected.request_id[1:])
    if case == "malformed":
        inner += " trailing"
    inner_whitespace = {
        "inner-leading-nbsp": "\u00a0", "inner-leading-vtab": "\v",
        "inner-trailing-nbsp": "\u00a0", "inner-trailing-vtab": "\v",
        "json-whitespace": " \t\r\n",
    }
    if case in inner_whitespace:
        whitespace = inner_whitespace[case]
        inner = inner + whitespace if "trailing" in case else whitespace + inner
    text = json.dumps({"result": inner}, ensure_ascii=ensure_ascii, separators=(",", ":"))
    if case == "unicode":
        text = json.dumps({"result": inner}, ensure_ascii=False, separators=(",", ":"))
    if case == "wrong-envelope":
        text = json.dumps({"content": [{"text": inner}]})
    if case == "outer-duplicate":
        text = '{"result":' + json.dumps(inner) + ',"result":' + json.dumps(inner) + '}'
    outer_whitespace = {
        "outer-leading-nbsp": "\u00a0", "outer-leading-vtab": "\v",
        "outer-trailing-nbsp": "\u00a0", "outer-trailing-vtab": "\v",
        "json-whitespace": " \t\r\n",
    }
    if case in outer_whitespace:
        whitespace = outer_whitespace[case]
        text = text + whitespace if "trailing" in case else whitespace + text
    digest = sha256(text.encode()).hexdigest()
    if case == "copy-edited":
        text += " "
    if case == "spilled":
        text = "Result saved to local artifact"
    if case == "digest":
        digest_change = True
    return operation, text, "0" * 64 if digest_change else digest


@pytest.mark.parametrize("case", [
    "submit", "read", "unicode", "unicode-escaped", "unknown-content", "wrong-request", "wrong-event",
    "wrong-target", "wrong-origin", "nested", "wrong-key", "duplicate", "duplicate-escaped-key",
    "nested-duplicate", "escaped-value", "malformed", "wrong-envelope", "outer-duplicate",
    "outer-leading-nbsp", "outer-leading-vtab", "outer-trailing-nbsp", "outer-trailing-vtab",
    "inner-leading-nbsp", "inner-leading-vtab", "inner-trailing-nbsp", "inner-trailing-vtab",
    "json-whitespace", "copy-edited", "spilled", "digest", "oversize", "boolean-id",
    "expected-work", "expected-session", "forged-expected", "ack", "unknown-operation", "read-without-reply", "submit-with-reply",
])
def test_presentation_returns_only_complete_exact_identifier_value_spans(case):
    api = _api()
    expected = api.expected_submit_ids(logical_session_id="local-session", target="codex", work_item_id="item-1")
    operation, text, digest = _presentation(case, expected)
    changed_facts = {
        "expected-work": lambda: api.expected_submit_ids(logical_session_id="local-session", target="codex", work_item_id="other-work"),
        "expected-session": lambda: api.expected_submit_ids(logical_session_id="other-session", target="codex", work_item_id="item-1"),
        "forged-expected": lambda: replace(expected, request_id="c2VjcmV0LXBheWxvYWQ="),
    }
    if case == "forged-expected":
        operation, text, digest = _presentation("submit", changed_facts[case]())
    if case in changed_facts:
        expected = changed_facts[case]()
    spans = api.verify_metadata_presentation(operation=operation, expected=expected,
                                             text=text, presentation_sha256=digest, max_bytes=65536)
    if case not in {"submit", "read", "unicode", "unicode-escaped", "unknown-content", "json-whitespace"}:
        assert spans == ()
        return
    assert {span.kind for span in spans} == {"request_id", "event_id"}
    raw = text.encode()
    for span in spans:
        assert raw[span.start:span.end] == span.value == getattr(expected, span.kind).encode()
        assert span.presentation_sha256 == digest
    assert spans[0].end <= spans[1].start
    if case == "unknown-content":
        remainder = raw
        for span in reversed(spans):
            remainder = remainder[:span.start] + b"<derived>" + remainder[span.end:]
        assert b"c2VjcmV0LXBheWxvYWQ=" in remainder
        assert b"token=synthetic-secret-value" in remainder
        assert expected.request_id.encode() in remainder  # Nested copy receives no span.
