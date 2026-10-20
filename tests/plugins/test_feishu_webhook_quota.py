"""Feishu delivery quota must be reserved for authenticated events (#29154)."""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from plugins.platforms.feishu import adapter as feishu


def make_adapter():
    adapter = object.__new__(feishu.FeishuAdapter)
    adapter._app_id = "test-app"
    adapter._webhook_path = "/feishu"
    adapter._verification_token = "test-token"
    adapter._encrypt_key = "test-key"
    adapter._webhook_rate_counts = {}
    adapter._webhook_ingress_rate_counts = {}
    adapter._webhook_anomaly_counts = {}
    adapter._on_message_event = Mock()
    return adapter


def make_request(payload, *, signed=True):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signed:
        timestamp, nonce = "1", "test-nonce"
        headers.update({
            "x-lark-request-timestamp": timestamp,
            "x-lark-request-nonce": nonce,
            "x-lark-signature": hashlib.sha256(
                (timestamp + nonce + "test-key").encode() + body
            ).hexdigest(),
        })
    return SimpleNamespace(
        remote="192.0.2.1", headers=headers, content_length=len(body),
        content=SimpleNamespace(readexactly=AsyncMock(
            side_effect=asyncio.IncompleteReadError(body, len(body) + 1),
        )),
    )


@pytest.mark.parametrize("failure", ["token", "signature", "json", "size"])
def test_rejected_request_preserves_last_delivery_slot(monkeypatch, failure):
    adapter = make_adapter()
    key = "test-app:/feishu:192.0.2.1"
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_RATE_LIMIT_MAX", 2)
    monkeypatch.setattr(feishu.time, "time", lambda: 100.0)
    adapter._webhook_rate_counts[key] = (1, 100.0)
    payload = {"header": {"token": "test-token", "event_type": "im.message.receive_v1"}}
    request = make_request(payload, signed=failure != "signature")
    expected = 401
    if failure == "token":
        request = make_request({"header": {"token": "invalid"}})
    elif failure == "json":
        request.content.readexactly.side_effect = asyncio.IncompleteReadError(b"invalid json", 100)
        expected = 400
    elif failure == "size":
        request.content_length = feishu._FEISHU_WEBHOOK_MAX_BODY_BYTES + 1
        expected = 413

    assert asyncio.run(adapter._handle_webhook_request(request)).status == expected
    assert adapter._webhook_rate_counts[key] == (1, 100.0)
    adapter._on_message_event.assert_not_called()
    assert asyncio.run(adapter._handle_webhook_request(make_request(payload))).status == 200
    assert adapter._webhook_rate_counts[key] == (2, 100.0)
    adapter._on_message_event.assert_called_once()
    assert asyncio.run(adapter._handle_webhook_request(make_request(payload))).status == 429
    adapter._on_message_event.assert_called_once()


@pytest.mark.parametrize("valid_token", [True, False])
def test_url_verification_preserves_delivery_quota(valid_token):
    adapter = make_adapter()
    request = make_request({
        "type": "url_verification", "challenge": "test-challenge",
        "token": "test-token" if valid_token else "invalid",
    }, signed=False)
    response = asyncio.run(adapter._handle_webhook_request(request))
    assert response.status == (200 if valid_token else 401)
    if valid_token:
        assert json.loads(response.text) == {"challenge": "test-challenge"}
    assert adapter._webhook_rate_counts == {}
    adapter._on_message_event.assert_not_called()


@pytest.mark.parametrize("credentials", ["token", "signature", "both"])
@pytest.mark.parametrize("exhaustion", ["peer", "table"])
def test_failed_request_budget_cannot_block_authenticated_delivery(monkeypatch, credentials, exhaustion):
    adapter = make_adapter()
    if credentials == "token":
        adapter._encrypt_key = ""
    elif credentials == "signature":
        adapter._verification_token = ""
    monkeypatch.setattr(feishu.time, "time", lambda: 100.0)
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_INGRESS_RATE_LIMIT_MAX", 1)
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_RATE_LIMIT_MAX", 1)
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_RATE_MAX_KEYS", 1)
    payload = {"header": {"token": "test-token", "event_type": "im.message.receive_v1"}}
    for expected in (401, 429):
        invalid = make_request({"token": "invalid"}, signed=False)
        if exhaustion == "table":
            invalid.remote = "198.51.100.1"
        assert asyncio.run(adapter._handle_webhook_request(invalid)).status == expected
    assert adapter._webhook_rate_counts == {}

    valid = make_request(payload, signed=credentials != "token")
    assert asyncio.run(adapter._handle_webhook_request(valid)).status == 200
    adapter._on_message_event.assert_called_once()
    assert asyncio.run(adapter._handle_webhook_request(
        make_request(payload, signed=credentials != "token"),
    )).status == 429
    adapter._on_message_event.assert_called_once()
