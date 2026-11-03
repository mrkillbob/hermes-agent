"""Actual sealed TurnRunner/SDK worker hold; no record permission or HTTP ACK."""
import httpx
import pytest
from unittest.mock import MagicMock

from agent.diagnostic_reference_policy import sealed_output_boundary

from tests.gateway.test_native_transport_commit import native
from tests.gateway.test_reference_observer_boundary import bridge


def test_actual_sdk_worker_holds_native_scope_through_client_cleanup(native, bridge, monkeypatch):
    make, requests, _ = bridge
    runner, agent = make(True)
    operation = native.issuer.issue_send(operation_id='fixture-physical', payload_sha256='a' * 64)
    agent._native_send_operation = operation  # private HOST input, never request/note fields
    agent._client_kwargs['max_retries'] = 0
    wire, closed = [], []
    handle = httpx.HTTPTransport.handle_request
    cleanup = agent._close_request_openai_client

    def observed_wire(transport, request):
        wire.append(operation.state)
        return handle(transport, request)

    def observed_cleanup(client, *, reason):
        closed.append(operation.state)
        return cleanup(client, reason=reason)

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', observed_wire)
    monkeypatch.setattr(agent, '_close_request_openai_client', observed_cleanup)
    result = runner._run_conversation_with_approval(agent, [], '', None, None)
    assert len(requests) == 1 and wire == ['active'] and closed == ['active']
    assert result['failed'] is False
    assert operation.receipt()['lease_released'] is True
    assert operation.receipt()['http_response_attested'] is False
    assert operation.receipt()['session_acknowledged'] is False


def test_native_send_uses_actual_zero_retry_client_not_requested_kwargs(native, bridge):
    make, requests, _ = bridge
    runner, agent = make(True)
    agent._native_send_operation = native.issuer.issue_send(
        operation_id='unqualified-retries', payload_sha256='a' * 64)
    agent._client_kwargs['max_retries'] = 1  # existing request-client factory deliberately forces zero
    result = runner._run_conversation_with_approval(agent, [], '', None, None)
    assert result['failed'] is False and len(requests) == 1


def test_unqualified_actual_retry_client_is_closed_before_any_send(native, bridge, monkeypatch):
    make, requests, _ = bridge
    runner, agent = make(True)
    operation = native.issuer.issue_send(operation_id='opaque-retries', payload_sha256='a' * 64)
    agent._native_send_operation = operation
    create, close = agent._create_request_openai_client, agent._close_request_openai_client
    closed = []

    def unqualified(*, reason, api_kwargs=None):
        client = create(reason=reason, api_kwargs=api_kwargs)
        client.max_retries = 1  # model an unsupported factory; production factory is left unchanged
        return client

    def cleanup(client, *, reason):
        closed.append(operation.state)
        return close(client, reason=reason)

    monkeypatch.setattr(agent, '_create_request_openai_client', unqualified)
    monkeypatch.setattr(agent, '_close_request_openai_client', cleanup)
    result = runner._run_conversation_with_approval(agent, [], '', None, None)
    assert result['failed'] is True and requests == [] and closed == ['active']
    assert operation.receipt()['state'] == 'unknown' and operation.receipt()['lease_released'] is True


@pytest.mark.parametrize('status', [400, 429, 503])
def test_actual_sdk_failure_is_terminal_unknown_without_retry(native, bridge, monkeypatch, status):
    make, requests, _ = bridge
    runner, agent = make(True)
    operation = native.issuer.issue_send(operation_id='failed-send', payload_sha256='a' * 64)
    agent._native_send_operation = operation
    agent._client_kwargs['max_retries'] = 0

    def failed_wire(transport, request):
        requests.append(True)
        assert operation.state == 'active'
        return httpx.Response(status, json={'error': {'message': 'INERT-PRIVATE-FAILURE'}})

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', failed_wire)
    result = runner._run_conversation_with_approval(agent, [], '', None, None)
    assert result['failed'] is True and requests == [True]
    assert operation.receipt()['state'] == 'unknown'
    assert operation.receipt()['lease_released'] is True


def test_cleanup_failure_cannot_return_a_successful_sdk_response(native, bridge, monkeypatch):
    make, requests, _ = bridge
    runner, agent = make(True)
    operation = native.issuer.issue_send(operation_id='cleanup-failure', payload_sha256='a' * 64)
    agent._native_send_operation = operation
    agent._client_kwargs['max_retries'] = 0
    close = agent._close_request_openai_client

    def failed_close(client, *, reason):
        assert operation.state == 'active'
        close(client, reason=reason)
        raise ValueError('INERT-PRIVATE-CLEANUP')

    monkeypatch.setattr(agent, '_close_request_openai_client', failed_close)
    result = runner._run_conversation_with_approval(agent, [], '', None, None)
    assert result['failed'] is True and len(requests) == 1
    assert operation.receipt()['state'] == 'unknown' and operation.receipt()['lease_released'] is True


@pytest.mark.parametrize('mode', ['stream', 'cron', 'unsealed'])
def test_unsupported_native_worker_path_refuses_before_sdk(native, bridge, monkeypatch, mode):
    make, requests, _ = bridge
    runner, agent = make(mode != 'unsealed')
    operation = native.issuer.issue_send(operation_id='unsupported', payload_sha256='a' * 64)
    agent._native_send_operation = operation
    agent._client_kwargs['max_retries'] = 0
    if mode == 'stream':
        agent._disable_streaming = False
    if mode == 'cron':
        agent.platform = 'cron'
    if mode == 'unsealed':
        monkeypatch.setattr('agent.background_review.cancel_background_review_for_live_turn',
                            lambda *args: pytest.fail('unsealed native operation reached setup'))
        with pytest.raises(RuntimeError, match='native_send_worker_unavailable'):
            runner._run_conversation_with_approval(agent, [], '', None, None)
        from agent.chat_completion_nonstream import _NonStreamRequest
        with pytest.raises(RuntimeError, match='native_send_worker_unavailable'):
            _NonStreamRequest(agent, {'model': 'fixture', 'messages': []})
    else:
        result = runner._run_conversation_with_approval(agent, [], '', None, None)
        assert result['failed'] is True
    assert requests == [] and operation.state == 'prepared'


def test_worker_control_exception_reaches_poller_and_releases_hold(native, bridge, monkeypatch):
    from agent.chat_completion_nonstream import _NonStreamRequest

    make, _, _ = bridge
    _, agent = make(True)
    operation = native.issuer.issue_send(operation_id='control', payload_sha256='a' * 64)
    agent._native_send_operation = operation
    agent._client_kwargs['max_retries'] = 0
    control = KeyboardInterrupt('inert operator interrupt')

    def interrupted_wire(transport, request):
        assert operation.state == 'active'
        raise control

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', interrupted_wire)

    @sealed_output_boundary
    def run(owner):
        return _NonStreamRequest(owner, {'model': 'fixture', 'messages': []}).run()

    with pytest.raises(KeyboardInterrupt) as caught:
        run(agent)
    assert caught.value is control
    assert operation.receipt()['state'] == 'unknown' and operation.receipt()['lease_released'] is True


def test_ordinary_dynamic_agent_does_not_invent_a_host_operation():
    from agent.native_send_worker import capture_native_send
    assert capture_native_send(MagicMock()) is None


def test_worker_pins_original_handle_before_agent_attribute_changes(native, bridge, monkeypatch):
    from agent.chat_completion_nonstream import _NonStreamRequest

    make, requests, _ = bridge
    runner, agent = make(True)
    original = native.issuer.issue_send(operation_id='original', payload_sha256='a' * 64)
    replacement = native.issuer.issue_send(operation_id='later', payload_sha256='a' * 64)
    agent._native_send_operation = original
    agent._client_kwargs['max_retries'] = 0
    call_body = _NonStreamRequest._call_body

    def changed(request):
        assert original.state == 'active'
        agent._native_send_operation = replacement
        return call_body(request)

    monkeypatch.setattr(_NonStreamRequest, '_call_body', changed)
    result = runner._run_conversation_with_approval(agent, [], '', None, None)
    assert result['failed'] is False and len(requests) == 1
    assert original.receipt()['lease_released'] is True and replacement.state == 'prepared'


def test_hold_entry_refusal_reaches_poller_without_a_client_or_send(native, bridge):
    make, requests, _ = bridge
    runner, agent = make(True)
    operation = native.issuer.issue_send(operation_id='closed-before-worker', payload_sha256='a' * 64)
    agent._native_send_operation = operation
    agent._client_kwargs['max_retries'] = 0
    native.issuer.begin_close()
    result = runner._run_conversation_with_approval(agent, [], '', None, None)
    assert result['failed'] is True and requests == []
    assert operation.state == 'prepared' and operation.receipt()['lease_released'] is False
