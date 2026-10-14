"""Fake endpoints establish seam limits without credentials or model calls."""
import io
import json

import pytest

import tests.plugins.peer_transport_loader  # noqa: F401  (registers peer_transport_plugin)

from peer_transport_plugin.adapters import OwnedStream, QueueEndpoint
from peer_transport_plugin.transport import Delivery, Envelope, Route, TransportError


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


PEERS = ("mira", "claude", "hermes", "codex", "stackchan", "homelab", "lunabot")


@pytest.mark.parametrize("sender,recipient", [(a, b) for a in PEERS for b in PEERS if a != b])
def test_bound_fake_endpoints_round_trip_after_lost_ack_without_model_or_hub(tmp_path, sender, recipient):
    from peer_transport_plugin.transport import Mailbox
    outgoing = Mailbox(tmp_path / "out.db")
    incoming = Mailbox(tmp_path / "in.db")
    return_mail = Mailbox(tmp_path / "return.db")
    bound = Route(recipient, "recipient-session", 1, "selected-conversation")
    reply_bound = Route(sender, "sender-session", 1, "selected-conversation")
    request = Envelope("request", sender, recipient, "selected-conversation", "task-nonce",
                       "task_request", b"offline-benign-nonce", 10, 100)
    handled = []

    def persist_then_queue(wire, binding):
        assert binding == bound.binding
        original = Envelope.decode(wire)
        if incoming.receive(original, bound, authenticated_sender=sender, now=11):
            assert incoming.begin_handling(original.message_id, bound, now=11)
            handled.append(original)
        return True

    endpoint = QueueEndpoint(bound, persist_then_queue)
    outgoing.store(request, bound)
    first = outgoing.claim(bound, now=11, lease_seconds=1)
    assert endpoint.send(first) == "queued"
    # Simulate a lost admission/storage acknowledgement and transport restart.
    outgoing.close()
    outgoing = Mailbox(tmp_path / "out.db")
    replay = outgoing.claim(bound, now=13, lease_seconds=1)
    assert replay.envelope == request
    assert endpoint.send(replay) == "queued"
    assert handled == [request]
    ack = incoming.finish_handling("request", bound, stage="processed")
    outgoing.acknowledge(replay, ack, authenticated_peer=recipient, now=13)
    reply = Envelope("reply", recipient, sender, request.conversation_id, request.correlation_id,
                     "task_result", request.payload, 13, 100, in_reply_to=request.message_id)
    incoming.store(reply, reply_bound)
    returning = incoming.claim(reply_bound, now=14)
    return_endpoint = QueueEndpoint(reply_bound, lambda wire, binding:
                                   return_mail.receive(Envelope.decode(wire), reply_bound,
                                                       authenticated_sender=recipient, now=14))
    assert return_endpoint.send(returning) == "queued"
    assert return_mail.status("reply", direction="in")["payload"] == request.payload
    assert reply.sender == recipient and reply.recipient == sender
    assert reply.in_reply_to == request.message_id
    assert not outgoing.status("request")["accepted"]
    outgoing.close()
    incoming.close()
    return_mail.close()


def result_frame(uuid="result-1"):
    return json.dumps({"type": "result", "session_id": "session-1", "uuid": uuid,
                       "is_error": False, "result": "nonce"}).encode()


def test_raw_stream_does_not_readmit_completed_input_before_receipt(tmp_path):
    from peer_transport_plugin.transport import Mailbox
    box = Mailbox(tmp_path / "mail.db")
    box.store(delivery().envelope, delivery().route)
    pipe = io.StringIO()
    adapter = OwnedStream(pipe, delivery().route)
    first = box.claim(delivery().route, now=11, lease_seconds=1)
    adapter.send(first)
    assert adapter.observe(result_frame()).stage == "processed"
    replay = box.claim(delivery().route, now=13)
    with pytest.raises(TransportError):
        adapter.send(replay)
    assert len(pipe.getvalue().splitlines()) == 1


def test_bound_stream_busy_defers_unoffered_mail_without_burning_attempts(tmp_path):
    from peer_transport_plugin.transport import Mailbox
    box = Mailbox(tmp_path / "mail.db", max_attempts=2)
    box.store(delivery().envelope, delivery().route)
    box.store(delivery(message_id="m2").envelope, delivery().route)
    adapter = OwnedStream(io.StringIO(), delivery().route, mailbox=box)
    first = box.claim(delivery().route, now=11, lease_seconds=1)
    adapter.send(first)
    for now in (13, 15, 17):
        waiting = box.claim(delivery().route, now=now, lease_seconds=1)
        assert waiting.envelope.message_id == "m2"
        with pytest.raises(TransportError):
            adapter.send(waiting)
        assert box.status("m2")["attempts"] == 0
    observed = adapter.observe(result_frame())
    box.evidence(observed.delivery, "processed", now=30)
    assert box.status("m1")["processed"]
    assert box.status("m1")["attempts"] == 1
    assert box.status("m2")["disposition"] == "pending"


def test_durable_stream_holds_before_io_and_does_not_replay_after_result(tmp_path):
    from peer_transport_plugin.transport import Mailbox
    box = Mailbox(tmp_path / "mail.db")
    box.store(delivery().envelope, delivery().route)
    box.store(delivery(message_id="m2").envelope, delivery().route)
    pipe = io.StringIO()
    adapter = OwnedStream(pipe, delivery().route, mailbox=box)
    first = adapter.deliver_next(now=11, lease_seconds=1)
    assert first.envelope.message_id == "m1"
    assert adapter.deliver_next(now=13) is None
    assert box.status("m2")["attempts"] == 0
    assert box.status("m1")["disposition"] == "awaiting_result"
    assert not box.status("m1")["queued"]
    echoed = adapter.observe(json.dumps({"type": "user", "session_id": "session-1", "uuid": "m1"}).encode())
    box.evidence(echoed.delivery, echoed.stage, now=14)
    assert box.status("m1")["disposition"] == "awaiting_result"
    assert box.status("m1")["queued"]
    observed = adapter.observe(result_frame())
    # Persist the receipt before another input; memory alone is not restart evidence.
    assert adapter.deliver_next(now=15) is None
    assert box.status("m2")["attempts"] == 0
    box.evidence(observed.delivery, "processed", now=16)
    assert box.status("m1")["processed"]
    assert adapter.deliver_next(now=17).envelope.message_id == "m2"
    assert [json.loads(line)["uuid"] for line in pipe.getvalue().splitlines()] == ["m1", "m2"]


def test_uncertain_partial_stream_write_remains_held_after_mailbox_restart(tmp_path):
    from peer_transport_plugin.transport import Mailbox
    class PartialPipe(io.StringIO):
        def write(self, wire):
            return super().write(wire[:5])
    path = tmp_path / "mail.db"
    box = Mailbox(path)
    box.store(delivery().envelope, delivery().route)
    adapter = OwnedStream(PartialPipe(), delivery().route, mailbox=box)
    with pytest.raises(TransportError):
        adapter.deliver_next(now=11)
    box.close()
    box = Mailbox(path)
    assert box.claim(delivery().route, now=50) is None
    assert box.status("m1")["disposition"] == "awaiting_result"
    assert not box.status("m1")["queued"] and not box.status("m1")["processed"]


def test_recreated_bound_stream_cannot_pass_uncertain_session_mail(tmp_path):
    from peer_transport_plugin.transport import Mailbox
    path = tmp_path / "mail.db"
    box = Mailbox(path)
    box.store(delivery().envelope, delivery().route)
    box.store(delivery(message_id="m2").envelope, delivery().route)
    old = OwnedStream(io.StringIO(), delivery().route, mailbox=box)
    first = old.deliver_next(now=11, lease_seconds=1)
    box.close()
    box = Mailbox(path)
    pipe = io.StringIO()
    recreated = OwnedStream(pipe, delivery().route, mailbox=box)
    assert recreated.deliver_next(now=30) is None
    assert box.status("m2")["attempts"] == 0
    assert pipe.getvalue() == ""
    # Even a direct claim/send cannot bypass persisted route-level backpressure.
    other = box.claim(delivery().route, now=31)
    with pytest.raises(TransportError):
        recreated.send(other)
    assert box.status("m2")["attempts"] == 0
    assert recreated.observe(result_frame()) is None
    observed = old.observe(result_frame())
    assert observed.delivery == first
    box.evidence(observed.delivery, "processed", now=32)
    assert recreated.deliver_next(now=33).envelope.message_id == "m2"


@pytest.fixture
def launch_contract(tmp_path):
    import hashlib
    import os
    from peer_transport_plugin import adapters
    binary = tmp_path / 'claude'
    binary.write_bytes(b'fake executable, never executed')
    return adapters.ClaudeLaunchContext(
        str(binary), hashlib.sha256(binary.read_bytes()).hexdigest(),
        os.getuid(), os.environ['HOME'], os.environ.get('SHELL', ''),
        str(__import__('pathlib').Path.cwd()), frozenset({'pro', 'max'}))


def auth_status(**changes):
    record = dict(loggedIn=True, authMethod='claude.ai', apiProvider='firstParty',
                  subscriptionType='pro', email='private@example.invalid',
                  orgId='private-org', configDirectory='private-path')
    record.update(changes)
    return record


class FakeClaudeProcess:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.returncode = None
        self.terminated = False
        self.killed = False
    def wait(self, timeout=None):
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode
    def terminate(self):
        self.terminated = True
    def kill(self):
        self.killed = True


def fake_launch(monkeypatch, status, exit_code=0):
    from peer_transport_plugin import adapters
    import subprocess
    calls = []
    child = FakeClaudeProcess()
    def run(args, **kwargs):
        calls.append(('preflight', args, kwargs))
        return subprocess.CompletedProcess(args, exit_code, json.dumps(status), 'private stderr')
    def spawn(args, **kwargs):
        calls.append(('spawn', args, kwargs))
        return child
    monkeypatch.setattr(adapters.subprocess, 'run', run)
    monkeypatch.setattr(adapters.subprocess, 'Popen', spawn)
    return calls, child


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('changes', [dict(loggedIn=False), dict(loggedIn=1),
    dict(authMethod='apiKey'), dict(apiProvider='bedrock'),
    dict(subscriptionType=None), dict(subscriptionType='unknown')])
def test_launcher_blocks_unqualified_context_without_leaking_status(launch_contract, monkeypatch, changes):
    from peer_transport_plugin.adapters import ClaudeProcess
    calls, _ = fake_launch(monkeypatch, auth_status(**changes))
    with pytest.raises(TransportError, match='execution_context_unavailable') as error:
        ClaudeProcess.launch(launch_contract, Route('claude', 'cfde2348-af28-45ac-811b-150279d0eccd', 1, 'conversation'))
    assert [call[0] for call in calls] == ['preflight']
    assert 'private' not in str(error.value)
    assert 'login' not in str(error.value).lower()


@pytest.mark.platforms('posix')
def test_launcher_uses_same_filtered_context_and_no_substantive_cutoffs(launch_contract, monkeypatch):
    from peer_transport_plugin.adapters import ClaudeProcess
    for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
                 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY'):
        monkeypatch.setenv(name, 'never-print')
    calls, child = fake_launch(monkeypatch, auth_status(subscriptionType='max'))
    proc = ClaudeProcess.launch(launch_contract, Route('claude', 'cfde2348-af28-45ac-811b-150279d0eccd', 1, 'conversation'))
    import subprocess
    assert calls[0][2].get('stdin') == subprocess.DEVNULL
    assert calls[1][2]['stdin'] == subprocess.PIPE
    assert calls[0][2]['env'] == calls[1][2]['env']
    assert calls[0][2]['cwd'] == calls[1][2]['cwd'] == launch_contract.cwd
    assert not any(name.startswith('ANTHROPIC') or name.startswith('CLAUDE') for name in calls[1][2]['env'])
    args = calls[1][1]
    assert '--restricted' in args and args[args.index('--tools') + 1] == ''
    assert args[args.index('--mcp-config') + 1] == '{"mcpServers":{}}'
    assert not set(args).intersection({'--max-budget-usd', '--max-turns', '--max-tokens', '--effort'})
    assert not child.stdin.closed
    proc.cancel()
    assert child.terminated and child.stdin.closed and child.stdout.closed


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('field,value', [('sha256', '0' * 64), ('uid', -1), ('home', '/wrong'), ('shell', '/wrong'), ('cwd', '/wrong')])
def test_launcher_rejects_host_contract_mismatch_before_auth(launch_contract, monkeypatch, field, value):
    from dataclasses import replace
    from peer_transport_plugin.adapters import ClaudeProcess
    calls, _ = fake_launch(monkeypatch, auth_status())
    with pytest.raises(TransportError, match='execution_context_unavailable'):
        ClaudeProcess.launch(replace(launch_contract, **{field: value}), delivery().route)
    assert not calls


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('usage', [{}, {'sonnet': {'provider': 'api'}}, {'sonnet': {'provider': 'firstParty'}, 'other': {'provider': 'bedrock'}}])
def test_launcher_holds_non_subscription_result_and_reaps(launch_contract, monkeypatch, usage):
    from dataclasses import replace
    from peer_transport_plugin.adapters import ClaudeProcess
    _, child = fake_launch(monkeypatch, auth_status())
    route = Route('claude', 'cfde2348-af28-45ac-811b-150279d0eccd', 1, 'conversation')
    proc = ClaudeProcess.launch(launch_contract, route)
    proc.stream.send(replace(delivery(), route=route))
    with pytest.raises(TransportError, match='provider_unverified'):
        proc.observe(json.dumps(dict(type='result', session_id=route.binding, uuid='r1', is_error=False, modelUsage=usage)).encode())
    assert proc.stream.active is not None
    assert child.terminated and child.stdout.closed


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('exit_code,expected', [(0, 'completed'), (1, 'process_failed')])
def test_launcher_requires_correlated_result_and_exit(launch_contract, monkeypatch, exit_code, expected):
    from dataclasses import replace
    from peer_transport_plugin.adapters import ClaudeProcess
    _, child = fake_launch(monkeypatch, auth_status())
    route = Route('claude', 'cfde2348-af28-45ac-811b-150279d0eccd', 1, 'conversation')
    proc = ClaudeProcess.launch(launch_contract, route)
    proc.stream.send(replace(delivery(), route=route))
    event = dict(type='result', session_id=route.binding, uuid='r1', is_error=False, modelUsage={'sonnet': {'provider':'firstParty'}})
    assert proc.observe(json.dumps({**event, 'session_id':'other'}).encode()) is None
    assert proc.observe(json.dumps(event).encode()).outcome == 'completed'
    assert not child.stdin.closed
    child.returncode = exit_code
    assert proc.finish().outcome == expected
    assert child.stdin.closed and child.stdout.closed


@pytest.mark.platforms('posix')
def test_launcher_missing_result_cannot_complete(launch_contract, monkeypatch):
    from peer_transport_plugin.adapters import ClaudeProcess
    _, child = fake_launch(monkeypatch, auth_status())
    proc = ClaudeProcess.launch(launch_contract, Route('claude', 'cfde2348-af28-45ac-811b-150279d0eccd', 1, 'conversation'))
    assert proc.finish().outcome == 'result_missing'
    assert child.stdout.closed


@pytest.mark.platforms('posix')
def test_launcher_invalid_auth_output_never_spawns_or_leaks(launch_contract, monkeypatch):
    from peer_transport_plugin.adapters import ClaudeProcess
    import peer_transport_plugin.adapters as adapters
    import subprocess
    calls, _ = fake_launch(monkeypatch, auth_status())
    monkeypatch.setattr(adapters.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, 'private malformed', 'private stderr'))
    with pytest.raises(TransportError) as error:
        ClaudeProcess.launch(launch_contract, Route('claude', 'cfde2348-af28-45ac-811b-150279d0eccd', 1, 'conversation'))
    assert 'private' not in str(error.value)
    assert not calls


@pytest.mark.platforms('posix')
def test_launcher_cancel_kills_reaps_and_preserves_uncertain_reservation(launch_contract, monkeypatch, tmp_path):
    from dataclasses import replace
    from peer_transport_plugin.adapters import ClaudeProcess
    from peer_transport_plugin.transport import Mailbox
    import subprocess
    _, child = fake_launch(monkeypatch, auth_status())
    original_wait = child.wait
    def wait(timeout=None):
        if not child.killed:
            raise subprocess.TimeoutExpired('private argv', timeout)
        return original_wait(timeout)
    child.wait = wait
    route = Route('claude', 'cfde2348-af28-45ac-811b-150279d0eccd', 1, 'conversation')
    box = Mailbox(tmp_path / 'mail.db')
    box.store(delivery().envelope, route)
    proc = ClaudeProcess.launch(launch_contract, route, mailbox=box)
    proc.stream.deliver_next(now=11)
    proc.cancel(cleanup_timeout=0.01)
    assert child.killed and child.returncode == 0
    assert box.status('m1')['disposition'] == 'awaiting_result'
    assert box.claim_stream(route, now=50) is None
    assert child.stdin.closed and child.stdout.closed


@pytest.mark.platforms('posix')
def test_launcher_operational_timeout_is_non_success_and_cleans_up(launch_contract, monkeypatch):
    from peer_transport_plugin.adapters import ClaudeProcess
    import subprocess
    _, child = fake_launch(monkeypatch, auth_status())
    original_wait = child.wait
    def wait(timeout=None):
        if not child.terminated:
            raise subprocess.TimeoutExpired('private argv', timeout)
        return original_wait(timeout)
    child.wait = wait
    proc = ClaudeProcess.launch(launch_contract, Route('claude', 'cfde2348-af28-45ac-811b-150279d0eccd', 1, 'conversation'))
    assert proc.finish(timeout=0.01).outcome == 'process_timeout'
    assert child.terminated and child.stdout.closed


@pytest.mark.platforms('posix')
def test_launcher_instructs_recipient_to_decode_without_granting_authority(launch_contract, monkeypatch):
    from peer_transport_plugin.adapters import ClaudeProcess
    calls, _ = fake_launch(monkeypatch, auth_status())
    proc = ClaudeProcess.launch(launch_contract, Route('claude', 'cfde2348-af28-45ac-811b-150279d0eccd', 1, 'conversation'))
    args = calls[-1][1]
    assert '--append-system-prompt' in args
    instruction = args[args.index('--append-system-prompt') + 1]
    assert 'base64' in instruction and 'original sender' in instruction
    assert 'approval' in instruction and 'grant' in instruction
    proc.cancel()


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('operation', ['finish', 'cancel'])
def test_launcher_broken_pipe_close_cannot_skip_reaping(launch_contract, monkeypatch, operation):
    from peer_transport_plugin.adapters import ClaudeProcess
    _, child = fake_launch(monkeypatch, auth_status())
    class BrokenClose(io.StringIO):
        def close(self):
            super().close()
            raise BrokenPipeError('private pipe detail')
    child.stdin = BrokenClose()
    proc = ClaudeProcess.launch(launch_contract, Route('claude', 'cfde2348-af28-45ac-811b-150279d0eccd', 1, 'conversation'))
    getattr(proc, operation)()
    assert child.returncode == 0
    assert child.stdin.closed and child.stdout.closed


@pytest.mark.platforms('posix')
def test_launcher_terminate_race_still_reaps_and_closes_pipes(launch_contract, monkeypatch):
    from peer_transport_plugin.adapters import ClaudeProcess
    _, child = fake_launch(monkeypatch, auth_status())
    def terminate():
        raise ProcessLookupError('private detail')
    child.terminate = terminate
    proc = ClaudeProcess.launch(launch_contract, Route('claude', 'cfde2348-af28-45ac-811b-150279d0eccd', 1, 'conversation'))
    proc.cancel()
    assert child.returncode == 0
    assert child.stdin.closed and child.stdout.closed
