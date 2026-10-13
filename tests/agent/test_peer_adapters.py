"""Fake endpoints establish seam limits without credentials or model calls."""
import io
import json

import pytest

from agent.peer_adapters import OwnedStream, QueueEndpoint
from agent.peer_transport import Delivery, Envelope, Route, TransportError


def delivery(recipient="claude", **changes):
    from dataclasses import replace
    msg = replace(Envelope("m1", "mira", recipient, "conversation", "correlation",
                           "message", b"benign-nonce", 10, 100), **changes)
    return Delivery(msg, Route(recipient, "session-1", 1, "conversation"), "lease-1")


def test_owned_pipe_preserves_envelope_keeps_stdin_open_and_serializes():
    pipe = io.StringIO()
    adapter = OwnedStream(pipe, delivery().route)
    assert adapter.send(delivery()) == "queued"
    assert not pipe.closed
    frame = json.loads(pipe.getvalue())
    assert frame["session_id"] == "session-1"
    assert Envelope.decode(frame["message"]["content"].encode()) == delivery().envelope
    with pytest.raises(TransportError):
        adapter.send(delivery(message_id="m2"))


def test_echo_is_admission_only_and_result_is_handling_not_work_acceptance():
    adapter = OwnedStream(io.StringIO(), delivery().route)
    adapter.send(delivery())
    echo = {"type": "user", "session_id": "session-1", "uuid": "m1"}
    assert adapter.observe(json.dumps(echo).encode()).stage == "queued"
    assert adapter.observe(b'{"type":"result","session_id":"another"}') is None
    result = b'{"type":"result","session_id":"session-1","uuid":"result-1","is_error":false,"result":"benign-nonce"}'
    observed = adapter.observe(result)
    assert observed.stage == "processed"
    assert observed.delivery.envelope.message_id == "m1"
    assert observed.raw == result
    assert observed.outcome == "completed"
    assert adapter.observe(result) is None


def test_failed_result_keeps_failure_distinct_and_reconnect_rejects_old_binding():
    adapter = OwnedStream(io.StringIO(), delivery().route)
    adapter.send(delivery())
    observed = adapter.observe(b'{"type":"result","session_id":"session-1","uuid":"result-1","is_error":true}')
    assert observed.outcome == "failed"
    from dataclasses import replace
    with pytest.raises(TransportError):
        adapter.send(replace(delivery(), route=Route("claude", "session-1", 2, "conversation")))


def test_queue_callback_receives_immutable_mail_but_boolean_is_only_queued():
    calls = []
    def queue(wire, binding):
        calls.append((wire, binding))
        return True
    adapter = QueueEndpoint(delivery("hermes").route, queue)
    assert adapter.send(delivery("hermes")) == "queued"
    assert Envelope.decode(calls[0][0]) == delivery("hermes").envelope
    assert calls[0][1] == "session-1"


def test_unsupported_routes_and_rejected_queue_never_report_delivery():
    adapter = QueueEndpoint(delivery("hermes").route, lambda wire, binding: False)
    with pytest.raises(TransportError):
        adapter.send(delivery("hermes"))
    with pytest.raises(TransportError):
        adapter.send(delivery("mira"))


def test_delayed_duplicate_result_does_not_complete_the_next_input():
    adapter = OwnedStream(io.StringIO(), delivery().route)
    adapter.send(delivery())
    result = b'{"type":"result","session_id":"session-1","uuid":"result-1","is_error":false,"result":"nonce"}'
    assert adapter.observe(result).stage == "processed"
    adapter.send(delivery(message_id="m2"))
    assert adapter.observe(result) is None
    next_result = result.replace(b'"result-1"', b'"result-2"')
    assert adapter.observe(next_result).delivery.envelope.message_id == "m2"


def test_malformed_result_never_establishes_processing():
    adapter = OwnedStream(io.StringIO(), delivery().route)
    adapter.send(delivery())
    with pytest.raises(TransportError):
        adapter.observe(b'{"type":"result","session_id":"session-1"}')
    with pytest.raises(TransportError):
        adapter.send(delivery(message_id="m2"))
