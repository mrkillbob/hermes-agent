"""Offline peer delivery: durable facts never imply task authority."""
from dataclasses import replace

import pytest

import tests.plugins.peer_transport_loader  # noqa: F401  (registers peer_transport_plugin)

from peer_transport_plugin.transport import Envelope, Mailbox, Route, TransportError


def message(**changes):
    return replace(Envelope("m1", "mira", "claude", "conversation", "c1",
                            "task_request", b"original\x00content", 10, 100), **changes)


def route(**changes):
    return replace(Route("claude", "session-1", 1, "conversation"), **changes)


def test_restart_lost_ack_replays_same_identity_and_receiver_deduplicates(tmp_path):
    sender = Mailbox(tmp_path / "send.db")
    receiver = Mailbox(tmp_path / "receive.db")
    msg = message()
    sender.store(msg, route())
    first = sender.claim(route(), now=11, lease_seconds=5)
    assert first.envelope == msg
    assert receiver.receive(msg, route(), authenticated_sender="mira", now=11)
    sender.close()
    sender = Mailbox(tmp_path / "send.db")
    replay = sender.claim(route(), now=17, lease_seconds=5)
    assert replay.envelope == msg
    assert not receiver.receive(msg, route(), authenticated_sender="mira", now=17)
    assert receiver.begin_handling(msg.message_id, route(), now=18)
    assert not receiver.begin_handling(msg.message_id, route(), now=18)
    sender.evidence(replay, "recipient_stored", now=18)
    assert sender.claim(route(), now=30) is None
    facts = sender.status(msg.message_id)
    assert facts["sender_stored"] and facts["recipient_stored"]
    assert not facts["processed"] and not facts["accepted"]


def test_conflicting_identity_and_spoofed_sender_are_rejected(tmp_path):
    box = Mailbox(tmp_path / "mail.db")
    box.store(message(), route())
    with pytest.raises(TransportError):
        box.store(message(payload=b"replacement"), route())
    with pytest.raises(TransportError):
        box.receive(message(), route(), authenticated_sender="hermes", now=11)
    assert box.status("m1")["payload"] == b"original\x00content"


@pytest.mark.parametrize("kind", ["message", "task_request", "task_result", "failure",
                                 "needs_input", "input_response", "cancel_request", "cancel_result"])
def test_all_kinds_preserve_content_and_correlation(tmp_path, kind):
    msg = message(kind=kind, in_reply_to="previous", generation="request-1")
    box = Mailbox(tmp_path / "mail.db")
    box.store(msg, route())
    assert box.claim(route(), now=11).envelope == msg
    assert Envelope.decode(msg.encode()) == msg


def test_expiry_retry_exhaustion_and_rotated_routes_hold_visibly(tmp_path):
    box = Mailbox(tmp_path / "mail.db", max_attempts=2)
    box.store(message(), route())
    assert box.claim(route(), now=11, lease_seconds=1)
    assert box.claim(route(), now=13, lease_seconds=1)
    assert box.claim(route(), now=15) is None
    assert box.status("m1")["disposition"] == "retry_exhausted"
    box.store(message(message_id="m2"), route())
    assert box.claim(route(binding="new", epoch=2), now=16) is None
    assert box.status("m2")["disposition"] == "route_changed"
    box.store(message(message_id="m3"), route())
    assert box.claim(route(), now=100) is None
    assert box.status("m3")["disposition"] == "expired"


def test_stale_lease_cannot_ack_and_echo_does_not_promote_other_facts(tmp_path):
    box = Mailbox(tmp_path / "mail.db")
    box.store(message(), route())
    old = box.claim(route(), now=11, lease_seconds=1)
    current = box.claim(route(), now=13)
    with pytest.raises(TransportError):
        box.evidence(old, "processed", now=14)
    box.evidence(current, "queued", now=14)
    status = box.status("m1")
    assert status["queued"]
    assert not status["recipient_stored"] and not status["processed"]
    with pytest.raises(TransportError):
        box.evidence(current, "user_approved", now=14)


def test_untrusted_wire_versions_fields_size_and_missing_generation_rejected():
    import json
    raw = json.loads(message().encode())
    raw["permission"] = "all"
    with pytest.raises(TransportError):
        Envelope.decode(json.dumps(raw).encode())
    with pytest.raises(TransportError):
        message(version="9").encode()
    with pytest.raises(TransportError):
        message(payload=b"x" * 65537).encode()
    with pytest.raises(TransportError):
        message(kind="cancel_request").encode()


def test_recipient_processing_claim_survives_restart_without_reinvocation(tmp_path):
    path = tmp_path / "mail.db"
    box = Mailbox(path)
    box.receive(message(), route(), authenticated_sender="mira", now=11)
    assert box.begin_handling("m1", route(), now=12)
    box.close()
    box = Mailbox(path)
    assert not box.begin_handling("m1", route(), now=13)
    assert box.status("m1", direction="in")["disposition"] == "handling"


def test_authenticated_receipt_is_bound_to_digest_session_and_independent_facts(tmp_path):
    from peer_transport_plugin.transport import Acknowledgement
    box = Mailbox(tmp_path / "mail.db")
    msg = message()
    box.store(msg, route())
    attempt = box.claim(route(), now=11)
    ack = Acknowledgement("m1", msg.digest, route(), "processed")
    with pytest.raises(TransportError):
        box.acknowledge(attempt, ack, authenticated_peer="hermes", now=12)
    with pytest.raises(TransportError):
        box.acknowledge(attempt, replace(ack, digest="0" * 64), authenticated_peer="claude", now=12)
    assert Acknowledgement.decode(ack.encode()) == ack
    box.acknowledge(attempt, ack, authenticated_peer="claude", now=12)
    box.evidence(attempt, "queued", now=13)
    assert box.status("m1")["processed"]
    assert not box.status("m1")["accepted"]
    assert box.status("m1")["disposition"] == "delivered"
    assert box.claim(route(), now=60) is None


@pytest.mark.parametrize("sender,recipient", [(a, b) for a in ("mira", "claude", "hermes")
                                               for b in ("mira", "claude", "hermes") if a != b])
def test_each_direct_pair_preserves_sender_without_intermediary(tmp_path, sender, recipient):
    box = Mailbox(tmp_path / "mail.db")
    msg = message(sender=sender, recipient=recipient)
    bound = route(peer=recipient)
    box.store(msg, bound)
    assert box.claim(bound, now=11).envelope.sender == sender


def test_parallel_claims_from_two_connections_do_not_deliver_twice(tmp_path):
    path = tmp_path / "mail.db"
    first = Mailbox(path)
    second = Mailbox(path)
    first.store(message(), route())
    assert first.claim(route(), now=11)
    assert second.claim(route(), now=11) is None


def test_later_processing_fact_can_arrive_after_delivery_lease(tmp_path):
    box = Mailbox(tmp_path / "mail.db")
    box.store(message(), route())
    attempt = box.claim(route(), now=11, lease_seconds=2)
    box.evidence(attempt, "recipient_stored", now=12)
    box.evidence(attempt, "processed", now=60)
    assert box.status("m1")["processed"]


def test_receiver_does_not_start_expired_or_rotated_mail(tmp_path):
    box = Mailbox(tmp_path / "mail.db")
    box.receive(message(), route(), authenticated_sender="mira", now=11)
    assert not box.begin_handling("m1", route(epoch=2), now=12)
    box.receive(message(message_id="m2"), route(), authenticated_sender="mira", now=11)
    assert not box.begin_handling("m2", route(), now=100)


def test_receiver_commits_processing_receipt_and_fences_conversation(tmp_path):
    box = Mailbox(tmp_path / "mail.db")
    with pytest.raises(TransportError):
        box.store(message(conversation_id="unrelated"), route())
    box.receive(message(), route(), authenticated_sender="mira", now=11)
    assert box.begin_handling("m1", route(), now=12)
    ack = box.finish_handling("m1", route(), stage="processed")
    assert ack.stage == "processed"
    box.close()
    box = Mailbox(tmp_path / "mail.db")
    assert box.status("m1", direction="in")["processed"]
    with pytest.raises(TransportError):
        box.finish_handling("m1", route(epoch=2), stage="accepted")


def test_ambiguous_ack_and_malformed_controller_route_are_rejected():
    from peer_transport_plugin.transport import Acknowledgement
    ack = Acknowledgement("m1", message().digest, route(), "queued").encode()
    with pytest.raises(TransportError):
        Acknowledgement.decode(ack.replace(b'"stage":"queued"', b'"stage":"queued","stage":"accepted"'))
    with pytest.raises(TransportError):
        route(peer=1, binding={}).validate()


def test_claim_only_touches_the_selected_conversation(tmp_path):
    box = Mailbox(tmp_path / "mail.db")
    box.store(message(), route())
    other = route(conversation_id="another")
    box.store(message(message_id="other", conversation_id="another"), other)
    assert box.claim(other, now=11).envelope.message_id == "other"
    assert box.status("m1")["attempts"] == 0
    assert box.claim(route(), now=11).envelope.message_id == "m1"


def test_wire_does_not_silently_default_missing_version():
    import json
    from peer_transport_plugin.transport import Acknowledgement
    for record, decode in [(json.loads(message().encode()), Envelope.decode),
                           (json.loads(Acknowledgement("m1", message().digest, route(), "queued").encode()),
                            Acknowledgement.decode)]:
        record.pop("version")
        with pytest.raises(TransportError):
            decode(json.dumps(record).encode())


def test_stale_epoch_cannot_terminalize_newer_mail_or_lease(tmp_path):
    box = Mailbox(tmp_path / "mail.db")
    current = route(binding="new-session", epoch=2)
    box.store(message(), current)
    assert box.claim(route(), now=11) is None
    assert box.status("m1")["disposition"] == "pending"
    assert box.status("m1")["attempts"] == 0
    active = box.claim(current, now=12)
    assert active is not None
    assert box.claim(route(), now=100) is None
    assert box.status("m1")["token"] == active.token
    box.evidence(active, "processed", now=101)
    assert box.status("m1")["processed"]


def test_same_epoch_wrong_binding_cannot_destroy_mail(tmp_path):
    box = Mailbox(tmp_path / "mail.db")
    box.store(message(), route(binding="current"))
    assert box.claim(route(binding="other"), now=11) is None
    assert box.status("m1")["disposition"] == "pending"
    assert box.claim(route(binding="current"), now=11)


def test_stream_reservation_fences_competing_connections_before_io(tmp_path):
    path = tmp_path / "mail.db"
    first = Mailbox(path)
    other = Mailbox(path)
    first.store(message(), route())
    first.store(message(message_id="m2"), route())
    reserved = first.claim_stream(route(), now=11, lease_seconds=1)
    assert first.status("m1")["disposition"] == "stream_reserved"
    assert other.claim_stream(route(), now=50) is None
    assert other.status("m2")["attempts"] == 0
    assert not first.status("m1")["queued"]
    assert other.defer(reserved)
    assert first.status("m1")["attempts"] == 0
    renewed = first.claim_stream(route(), now=51)
    assert renewed.token != reserved.token
    with pytest.raises(TransportError):
        other.park(reserved)
    first.park(renewed)
    assert not other.defer(renewed)
    assert first.status("m1")["disposition"] == "awaiting_result"
