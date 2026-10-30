"""One frozen worker budget counts core/aux HTTP attempts, including SDK retries."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import httpx
from openai import OpenAI
import pytest

from agent import managed_request_budget as budgets


@pytest.fixture(autouse=True)
def isolated_budget(monkeypatch):
    monkeypatch.setattr(budgets, '_budget', None)


def bind(requests):
    budgets.bind_worker_request_budget({'policy': {'kanban_json': json.dumps({
        'execution_budget': {'wall_seconds': 60, 'output_bytes': 1024, 'requests': requests},
    })}})


def test_absent_budget_preserves_unsupported_client():
    budgets.bind_worker_request_budget({'policy': {}})
    client = object()
    assert budgets.prepare_provider_client(client) is client
    for _ in range(10):
        budgets.consume_provider_request()


def test_budget_cannot_rebind_or_follow_mutated_frame():
    bind(1)
    with pytest.raises(RuntimeError, match='already bound'):
        bind(4)
    budgets.consume_provider_request()
    with pytest.raises(InterruptedError):
        budgets.consume_provider_request()


def test_core_and_aux_clients_share_counter_and_sdk_retries():
    bind(3)
    sent = []

    def send(request):
        sent.append(request.url.path)
        return httpx.Response(500, json={'error': {'message': 'retry'}})

    def client():
        return budgets.prepare_provider_client(OpenAI(api_key='fixture', base_url='https://fixture.invalid/v1',
            max_retries=1, http_client=httpx.Client(transport=httpx.MockTransport(send))))

    # Both physical SDK attempts count, despite one logical call.
    with client() as core:
        with pytest.raises(Exception):
            core.with_options(max_retries=0).chat.completions.create(model='fixture', messages=[])
    # No sleeps: exercise SDK retries with its retry-delay seam fixed at zero.
    with client() as auxiliary:
        auxiliary._calculate_retry_timeout = lambda *args: 0
        with pytest.raises(Exception):
            auxiliary.chat.completions.create(model='fixture', messages=[])
    with client() as recovery:
        with pytest.raises(Exception):
            recovery.chat.completions.create(model='fixture', messages=[])
    assert len(sent) == 3


def test_gate_is_atomic_across_threads_and_stop_is_permanent():
    bind(4)

    def attempt(_):
        try:
            budgets.consume_provider_request()
            return True
        except InterruptedError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 4
    budgets.stop_provider_requests()
    assert attempt(None) is False


def test_stop_uses_existing_worker_control_and_refuses_unsupported_transport():
    bind(1)
    stopped = []
    budgets.register_request_stop(lambda: stopped.append(True))
    with pytest.raises(InterruptedError, match='uncounted_provider_transport'):
        budgets.prepare_provider_client(object())
    assert stopped == [True]
    with pytest.raises(InterruptedError):
        budgets.consume_provider_request()


def test_async_clients_and_redirects_share_the_same_ceiling():
    bind(2)
    sent = []

    async def exercise():
        def send(request):
            sent.append(str(request.url))
            return httpx.Response(307, headers={'location': '/redirect'})

        async with httpx.AsyncClient(transport=httpx.MockTransport(send), follow_redirects=True) as http:
            sdk = SimpleNamespace(_client=http)
            assert budgets.prepare_provider_client(sdk) is sdk
            budgets.prepare_provider_client(sdk)  # reuse must not attach a second gate
            with pytest.raises(InterruptedError):
                await http.post('https://fixture.invalid/start')

    asyncio.run(exercise())
    assert len(sent) == 2


def test_production_core_and_aux_dispatch_share_one_frozen_budget():
    from agent.chat_completion_helpers import _dispatch_nonstreaming_api_request
    from agent.auxiliary_client import _dispatch_auxiliary_request

    bind(2)
    sent = []

    def send(request):
        sent.append(request.url.path)
        return httpx.Response(200, json={'id': 'fixture', 'choices': [], 'model': 'fixture'})

    with OpenAI(api_key='fixture', base_url='https://fixture.invalid/v1', max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(send))) as client:
        agent = SimpleNamespace(api_mode='chat_completions', provider='nvidia', base_url=str(client.base_url))
        request = {'model': 'fixture', 'messages': []}
        core = lambda: _dispatch_nonstreaming_api_request(agent, request, make_client=lambda *args: client)
        core()
        _dispatch_auxiliary_request(client, request,
            lambda kwargs: client.chat.completions.create(**kwargs),
            provider='nvidia', model='fixture', api_mode='chat_completions')
        with pytest.raises(Exception):
            core()
    assert len(sent) == 2


def test_worker_control_stop_closes_future_admission():
    from agent.managed_worker import WorkerControls
    import threading

    bind(4)
    controls = WorkerControls.__new__(WorkerControls)
    controls.stopped = threading.Event()
    controls.agent = None
    controls.lock = threading.Lock()
    controls.clarifications = {}
    controls.stop()
    with pytest.raises(InterruptedError, match='worker_request_admission_closed'):
        budgets.consume_provider_request()
    original = {'final_response': '', 'completed': False, 'interrupted': True, 'turn_exit_reason': 'operator_stop'}
    assert budgets.stamp_request_budget_result(original) == original


def test_swallowed_budget_error_still_returns_interruption_receipt():
    from agent.managed_worker import WorkerControls, result_frame
    from hermes_cli.turn_exit import turn_exit_code
    import threading

    bind(1)
    controls = WorkerControls.__new__(WorkerControls)
    controls.stopped = threading.Event()
    controls.lock = threading.Lock()
    controls.clarifications = {}
    interruptions = []
    controls.agent = SimpleNamespace(interrupt=lambda: interruptions.append(True))
    budgets.register_request_stop(controls.stop)
    budgets.consume_provider_request()
    with pytest.raises(InterruptedError):
        budgets.consume_provider_request()
    assert controls.stopped.is_set()
    assert interruptions == [True]
    # A caller catching the exception cannot turn the execution back into success.
    result = result_frame({'final_response': 'partial reply', 'completed': True})
    assert result['completed'] is False
    assert result['interrupted'] is True
    assert result['turn_exit_reason'] == 'worker_request_budget_exhausted'
    assert result['failure_reason'] == 'worker_request_budget_exhausted'
    assert turn_exit_code(result, kanban_worker=True) == 130


@pytest.mark.parametrize('stream', [False, True])
def test_unsupported_core_transport_is_refused_before_native_dispatch(stream, monkeypatch):
    from agent import chat_completion_helpers as calls

    bind(1)
    sent = []
    monkeypatch.setattr(calls, '_bedrock_converse_call', lambda *args, **kwargs: sent.append(True))
    monkeypatch.setattr(calls, '_BedrockStream', lambda *args: SimpleNamespace(run=lambda: sent.append(True)))
    agent = SimpleNamespace(api_mode='bedrock_converse', provider='bedrock', _interrupt_requested=False)
    with pytest.raises(InterruptedError, match='uncounted_provider_transport'):
        if stream:
            calls.interruptible_streaming_api_call(agent, {})
        else:
            calls._dispatch_nonstreaming_api_request(agent, {}, make_client=None)
    assert sent == []


@pytest.mark.parametrize('value', [True, 0, -1, 5, 1.5, '2', None])
def test_bad_request_limits_fail_closed(value):
    with pytest.raises(ValueError):
        bind(value)
