"""Ingress work and authenticated deliveries have independent bounded budgets."""

import asyncio
from unittest.mock import Mock

import pytest

from plugins.platforms.feishu import adapter as feishu
from tests.plugins.test_feishu_webhook_quota import make_adapter, make_request


@pytest.mark.parametrize("failure", ["type", "size", "read", "json", "token", "signature"])
def test_ingress_budget_rejects_before_another_body_read(monkeypatch, failure):
    adapter = make_adapter()
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_INGRESS_RATE_LIMIT_MAX", 1, raising=False)
    monkeypatch.setattr(feishu.time, "time", lambda: 100.0)
    payload = {"header": {"token": "test-token", "event_type": "im.message.receive_v1"}}
    incoming = {"header": {"token": "invalid"}} if failure == "token" else payload
    request = make_request(incoming, signed=failure != "signature")
    request.headers["Content-Type"] = "text/plain" if failure == "type" else "application/json"
    if failure == "size":
        request.content_length = feishu._FEISHU_WEBHOOK_MAX_BODY_BYTES + 1
    read_error = {"read": TimeoutError, "json": asyncio.IncompleteReadError(b"invalid json", 100)}.get(failure)
    if read_error is not None:
        request.content.readexactly.side_effect = read_error
    expected = {"type": 415, "size": 413, "read": 408, "json": 400, "token": 401, "signature": 401}[failure]
    assert asyncio.run(adapter._handle_webhook_request(request)).status == expected
    assert adapter._webhook_rate_counts == {}

    next_request = make_request(payload)
    adapter._is_webhook_signature_valid = Mock(return_value=True)
    assert asyncio.run(adapter._handle_webhook_request(next_request)).status == 429
    next_request.content.readexactly.assert_not_awaited()
    adapter._is_webhook_signature_valid.assert_not_called()
    adapter._on_message_event.assert_not_called()
    assert adapter._webhook_rate_counts == {}


def test_ingress_window_reopens_without_spending_delivery_quota(monkeypatch):
    adapter = make_adapter()
    now = [100.0]
    monkeypatch.setattr(feishu.time, "time", lambda: now[0])
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_INGRESS_RATE_LIMIT_MAX", 1, raising=False)
    assert asyncio.run(adapter._handle_webhook_request(make_request({"token": "invalid"}))).status == 401
    assert adapter._webhook_rate_counts == {}
    now[0] += feishu._FEISHU_WEBHOOK_RATE_WINDOW_SECONDS
    payload = {"header": {"token": "test-token", "event_type": "im.message.receive_v1"}}
    assert asyncio.run(adapter._handle_webhook_request(make_request(payload))).status == 200
    assert adapter._webhook_rate_counts == {"test-app:/feishu:192.0.2.1": (1, now[0])}
    adapter._on_message_event.assert_called_once()


def test_unsigned_challenge_is_bounded_only_by_ingress(monkeypatch):
    adapter = make_adapter()
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_INGRESS_RATE_LIMIT_MAX", 1, raising=False)
    payload = {"type": "url_verification", "token": "test-token", "challenge": "inert-challenge"}
    response = asyncio.run(adapter._handle_webhook_request(make_request(payload, signed=False)))
    assert response.status == 200
    assert "inert-challenge" in response.text
    assert adapter._webhook_rate_counts == {}
    assert asyncio.run(adapter._handle_webhook_request(make_request(payload, signed=False))).status == 429
    adapter._on_message_event.assert_not_called()


@pytest.mark.parametrize("pre_auth", [False, True])
def test_rate_tables_deny_new_keys_and_reclaim_only_expired_keys(monkeypatch, pre_auth):
    adapter = make_adapter()
    now = [100.0]
    monkeypatch.setattr(feishu.time, "time", lambda: now[0])
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_RATE_MAX_KEYS", 2)
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_RATE_LIMIT_MAX", 2)
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_INGRESS_RATE_LIMIT_MAX", 2, raising=False)
    counts = adapter._webhook_ingress_rate_counts if pre_auth else adapter._webhook_rate_counts
    for key in ("first", "second"):
        assert adapter._check_webhook_rate_limit(key, pre_auth=pre_auth)
    before = dict(counts)
    assert not adapter._check_webhook_rate_limit("third", pre_auth=pre_auth)
    assert counts == before
    assert adapter._check_webhook_rate_limit("first", pre_auth=pre_auth)
    assert not adapter._check_webhook_rate_limit("first", pre_auth=pre_auth)
    now[0] += feishu._FEISHU_WEBHOOK_RATE_WINDOW_SECONDS
    assert adapter._check_webhook_rate_limit("third", pre_auth=pre_auth)
    assert counts == {"third": (1, now[0])}
    other = adapter._webhook_rate_counts if pre_auth else adapter._webhook_ingress_rate_counts
    assert other == {}


def test_anomaly_tracking_keeps_active_keys_and_reclaims_expired_keys(monkeypatch, caplog):
    adapter = make_adapter()
    now = [100.0]
    monkeypatch.setattr(feishu.time, "time", lambda: now[0])
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_RATE_MAX_KEYS", 2)
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_ANOMALY_TTL_SECONDS", 60)
    monkeypatch.setattr(feishu, "_FEISHU_WEBHOOK_ANOMALY_THRESHOLD", 2)
    adapter._record_webhook_anomaly("first", "401")
    adapter._record_webhook_anomaly("second", "400")
    adapter._record_webhook_anomaly("third", "429")
    assert set(adapter._webhook_anomaly_counts) == {"first", "second"}
    adapter._record_webhook_anomaly("first", "408")
    assert adapter._webhook_anomaly_counts["first"] == (2, "408", 100.0)
    assert "2 consecutive error responses (408)" in caplog.text
    now[0] += 60
    adapter._record_webhook_anomaly("third", "401")
    assert adapter._webhook_anomaly_counts == {"third": (1, "401", now[0])}


def test_authenticated_success_clears_its_anomaly_without_resetting_ingress():
    adapter = make_adapter()
    adapter._record_webhook_anomaly("192.0.2.1", "401")
    adapter._record_webhook_anomaly("other", "400")
    payload = {"header": {"token": "test-token", "event_type": "im.message.receive_v1"}}
    assert asyncio.run(adapter._handle_webhook_request(make_request(payload))).status == 200
    assert "192.0.2.1" not in adapter._webhook_anomaly_counts
    assert "other" in adapter._webhook_anomaly_counts
    assert adapter._webhook_ingress_rate_counts["test-app:/feishu:192.0.2.1"][0] == 1
