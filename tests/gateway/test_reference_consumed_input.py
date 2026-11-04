"""Original capture to the actual claimed SDK turn; synthetic local transport only."""
import json
import asyncio
from threading import Event, Lock
from collections import OrderedDict
from types import MethodType

import pytest
import pytest_asyncio

from tests.gateway import test_session_learning_admission as admission_fixtures
from tests.gateway import test_reference_observer_boundary as observer_fixtures
import hermes_state_runtime as runtime

captured, admit = admission_fixtures.captured, admission_fixtures.admit
bridge = observer_fixtures.bridge

pytestmark = pytest.mark.platforms('posix')


@pytest_asyncio.fixture
async def resident(captured, bridge):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    make, requests, _ = bridge
    _, parent = make(False)
    parent.session_id = 'session'
    from gateway.slash_commands import GatewaySlashCommandsMixin
    runner = captured.authority.runner
    runner._agent_cache_lock, runner._agent_cache = Lock(), OrderedDict({captured.live.route: (parent, 'fixture')})
    runner._running_agents = {}
    runner._cached_agent_for = MethodType(GatewaySlashCommandsMixin._cached_agent_for, runner)
    runner._resident_agent_for = MethodType(GatewaySlashCommandsMixin._resident_agent_for, runner)
    captured.authority.runner._consume_pending_native_image_paths = lambda key: []
    captured.live.source.user_name = 'fixture'
    captured.live.source.is_bot = False
    captured.live.source.chat_id = 'session'
    token = set_hermes_home_override(captured.ref.profile_id)
    try:
        yield captured, requests, parent
    finally:
        reset_hermes_home_override(token)


@pytest.mark.asyncio
async def test_original_record_reaches_actual_turn_and_body_free_settlement(resident):
    captured, requests, _ = resident
    prepared = captured.broker.prepare_execution(captured.connection,
        decision_id=captured.decision, request_id='fixture-request', conversation_history=[],
        destination='http://127.0.0.1:8000/v1', model='fixture')
    receipt = await admit(captured)
    assert prepared['session_acknowledged'] is False
    row, first = await captured.authority._claim_next(captured.ref, captured.live)
    assert row['admission_id'] == first['admission_id'] == receipt['admission_id']
    response, outcome = await captured.authority._execute_claimed(captured.ref, captured.live, row)
    assert response is None and outcome == 'completed'
    assert len(requests) == 1
    assert 'PRIVATE-SYNTHETIC-REFERENCE' in json.dumps(requests[0])
    result = captured.authority.pending_results[row['admission_id']]
    assert 'PRIVATE-SYNTHETIC-REFERENCE' not in json.dumps(result)
    proof = result['result']['curated_input_provenance']
    assert proof['payload_sha256'] == captured.metadata['payload_sha256']
    assert proof['serialized_input_observed'] is True
    assert proof['session_acknowledged'] is False
    from gateway.session_settlement_recovery import settle_with_retry
    settlement = {}
    await settle_with_retry(captured.authority, captured.live, captured.ref, row,
                            None, outcome, result, settlement)
    saved = settlement['settled']
    assert (saved['status'], saved['outcome']) == ('terminal', 'completed')
    with captured.db._read_ctx() as conn:
        assert 'PRIVATE-SYNTHETIC-REFERENCE' not in '\n'.join(conn.iterdump())
    assert runtime.get_session_admission(captured.db, admission_id=row['admission_id'])['status'] == 'terminal'


async def arm(resident):
    state, _, _ = resident
    state.broker.prepare_execution(state.connection, decision_id=state.decision,
        request_id='fixture-request', conversation_history=[],
        destination='http://127.0.0.1:8000/v1', model='fixture')
    return await admit(state)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['revoke', 'close', 'route', 'membership', 'resident', 'lost_handle', 'copy', 'expired'])
async def test_original_permission_loss_before_claim_never_reaches_provider(resident, change):
    import copy
    state, requests, _ = resident
    receipt = await arm(resident)
    import inspect

    async def detach():
        state.live.subscribers['other'] = object()
        await state.authority.detach(state.connection.actor, 'fixture-membership')

    def copied():
        state.authority._reference_executions[receipt['admission_id']] = copy.copy(
            state.authority._reference_executions[receipt['admission_id']])

    actions = {
        'revoke': lambda: state.broker.revoke_and_wait(state.connection,
            decision_id=state.decision, request_id='fixture-request'),
        'close': state.connection.close,
        'route': lambda: state.authority.fence_route(state.live.route),
        'membership': detach,
        'resident': lambda: setattr(state.authority.runner, '_resident_agent_for', lambda key: None),
        'lost_handle': state.authority._reference_executions.clear,
        'copy': copied,
        'expired': lambda: setattr(state.broker._decisions[state.decision], 'expires_at', 0),
    }
    changed = actions[change]()
    if inspect.isawaitable(changed):
        await changed
    row, _ = await state.authority._claim_next(state.ref, state.live)
    assert row is False and requests == []
    assert runtime.get_session_admission(state.db, admission_id=receipt['admission_id'])['outcome'] == 'cancelled'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['revoke', 'close', 'cancel'])
async def test_invalidation_joins_real_physical_worker_without_ack(resident, monkeypatch, caplog, change):
    import httpx
    from gateway.session_learning_consumer import ReferenceExecutionUnknown
    from gateway.session_runtime_workers import mutation_tasks
    state, requests, _ = resident
    await arm(resident)
    row, _ = await state.authority._claim_next(state.ref, state.live)
    entered, release = Event(), Event()
    original = httpx.HTTPTransport.handle_request

    def paused(transport, request):
        if request.url.path != '/v1/chat/completions':
            return original(transport, request)
        execution = state.authority._reference_executions[row['admission_id']]
        assert execution.native_send.state == 'active'
        entered.set()
        assert release.wait(5)
        return original(transport, request)

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', paused)
    executing = asyncio.create_task(state.authority._execute_claimed(state.ref, state.live, row))
    retiring = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        if change == 'revoke':
            retiring = asyncio.create_task(state.broker.revoke_and_wait(state.connection,
                decision_id=state.decision, request_id='fixture-request'))
        elif change == 'close':
            retiring = asyncio.create_task(state.connection.close())
        else:
            executing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await executing
        await asyncio.sleep(0)
        assert state.authority._side_agents
        if retiring is not None:
            assert not retiring.done()
        release.set()
        if change != 'cancel':
            with pytest.raises(ReferenceExecutionUnknown):
                await asyncio.wait_for(executing, 5)
        if retiring is not None:
            await asyncio.wait_for(retiring, 5)
        await asyncio.wait_for(asyncio.gather(*list(mutation_tasks(state.authority)),
                                            return_exceptions=True), 5)
        saved = runtime.get_session_admission(state.db, admission_id=row['admission_id'])
        assert saved['status'] == 'unknown' and len(requests) == 1
        assert state.authority._side_agents == {}
        assert row['admission_id'] not in state.authority.pending_results
        assert 'PRIVATE-SYNTHETIC-REFERENCE' not in caplog.text
    finally:
        release.set()
        await asyncio.gather(executing, *([retiring] if retiring is not None else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_cleanup_failure_is_body_free_unknown_not_a_result(resident, monkeypatch, caplog):
    from run_agent import AIAgent
    from agent.diagnostic_reference_policy import retained_policy
    from gateway.session_learning_consumer import ReferenceExecutionUnknown
    state, requests, _ = resident
    await arm(resident)
    row, _ = await state.authority._claim_next(state.ref, state.live)
    release = AIAgent.release_clients

    def fail_private(agent, *args, **kwargs):
        if retained_policy(agent) is not None:
            raise RuntimeError('PRIVATE-SYNTHETIC-REFERENCE cleanup error')
        return release(agent, *args, **kwargs)

    monkeypatch.setattr(AIAgent, 'release_clients', fail_private)
    with pytest.raises(ReferenceExecutionUnknown):
        await state.authority._execute_claimed(state.ref, state.live, row)
    assert runtime.get_session_admission(state.db, admission_id=row['admission_id'])['status'] == 'unknown'
    assert len(requests) == 1 and 'PRIVATE-SYNTHETIC-REFERENCE' not in caplog.text
