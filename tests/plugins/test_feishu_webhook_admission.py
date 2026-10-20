"""Bounded webhook authentication must precede trusted event admission."""

import asyncio
import json

import pytest

from tests.plugins.test_feishu_webhook_quota import make_adapter, make_request


@pytest.mark.parametrize("payload", [
    [], None, "text", {"header": []}, {"header": "invalid"}, {"header": 1},
    {"header": {"token": "test-token"}, "event": []},
    {"type": "url_verification", "token": "test-token", "challenge": {}},
])
def test_malformed_envelope_is_rejected_without_delivery(payload):
    adapter = make_adapter()
    response = asyncio.run(adapter._handle_webhook_request(make_request(payload)))
    assert response.status == 400
    assert adapter._webhook_rate_counts == {}
    adapter._on_message_event.assert_not_called()


@pytest.mark.parametrize("depth", [40, 1500])
def test_nested_json_has_controlled_rejection(depth):
    adapter = make_adapter()
    body = b'{"header":{"token":"test-token"},"event":' + b"[" * depth + b"0" + b"]" * depth + b"}"
    request = make_request({})
    request.content_length = len(body)
    request.content.readexactly.side_effect = asyncio.IncompleteReadError(body, len(body) + 1)
    assert asyncio.run(adapter._handle_webhook_request(request)).status == 400
    assert adapter._webhook_rate_counts == {}
    adapter._on_message_event.assert_not_called()


@pytest.mark.parametrize("credentials,signed,corrupt,status", [
    ("token", False, False, 200), ("both", False, False, 200),
    ("both", True, False, 200), ("both", True, True, 401),
    ("signature", False, False, 401), ("signature", True, False, 200),
    ("signature", True, True, 401), ("none", False, False, 401),
])
def test_challenge_requires_configured_authentication(credentials, signed, corrupt, status):
    adapter = make_adapter()
    if credentials in {"signature", "none"}:
        adapter._verification_token = ""
    if credentials in {"token", "none"}:
        adapter._encrypt_key = ""
    payload = {"type": "url_verification", "token": "test-token", "challenge": "inert-challenge"}
    request = make_request(payload, signed=signed)
    if corrupt:
        request.headers["x-lark-signature"] = "invalid"
    response = asyncio.run(adapter._handle_webhook_request(request))
    assert response.status == status
    if status == 200:
        assert json.loads(response.text) == {"challenge": "inert-challenge"}
    assert adapter._webhook_rate_counts == {}
    adapter._on_message_event.assert_not_called()


@pytest.mark.parametrize("kind,status", [
    ("utf8", 400), ("surrogate-token", 401), ("encrypted", 400), ("read-error", 400),
])
def test_invalid_wire_input_and_unsupported_encryption_do_not_dispatch(kind, status):
    adapter = make_adapter()
    payload = {"header": {"token": "test-token", "event_type": "im.message.receive_v1"}}
    if kind == "surrogate-token":
        payload["header"]["token"] = "\ud800"
    elif kind == "encrypted":
        payload["encrypt"] = "unsupported-envelope"
    request = make_request(payload)
    if kind == "utf8":
        request.content.readexactly.side_effect = asyncio.IncompleteReadError(b"\xff", 2)
    elif kind == "read-error":
        request.content.readexactly.side_effect = RuntimeError("failed body read")
    assert asyncio.run(adapter._handle_webhook_request(request)).status == status
    assert adapter._webhook_rate_counts == {}
    adapter._on_message_event.assert_not_called()
