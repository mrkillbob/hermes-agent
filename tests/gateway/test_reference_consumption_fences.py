"""Actual wire/commit/finalizer boundaries; no live connection or model."""
import asyncio
from contextlib import contextmanager
from threading import Event

import pytest

from tests.gateway import test_reference_consumed_input as fixtures
import hermes_state_runtime as runtime

resident, captured, bridge = fixtures.resident, fixtures.captured, fixtures.bridge
arm, admit = fixtures.arm, fixtures.admit
pytestmark = pytest.mark.platforms('posix')


@pytest.mark.asyncio
async def test_record_fence_spans_validated_hook_to_actual_transport(resident, monkeypatch):
    from agent.reference_input_witness import ReferenceInputWitness
    from gateway.run_agent_cache import GatewayAgentCacheMixin
    from gateway.session_learning_execution import settle_reference
    state, requests, _ = resident
    await arm(resident)
    row, _ = await state.authority._claim_next(state.ref, state.live)
    entered, release = Event(), Event()
    original = ReferenceInputWitness.request_hook

    def paused(witness, request):
        original(witness, request)
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(ReferenceInputWitness, 'request_hook', paused)
    executing = asyncio.create_task(state.authority._execute_claimed(state.ref, state.live, row))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        epoch = state.authority.route_epoch(state.live.route)
        transitions = [lambda: state.authority.fence_route(state.live.route),
            lambda: state.authority._retire_overflowed('session', state.connection.transport),
            lambda: GatewayAgentCacheMixin._evict_cached_agent(state.authority.runner, state.live.route)]
        for transition in transitions:
            with pytest.raises(runtime.RuntimeStoreError, match='reference_change_busy'):
                transition()
        assert state.authority.route_epoch(state.live.route) == epoch
        assert 'fixture-membership' in state.live.subscribers and requests == []
        release.set()
        assert await asyncio.wait_for(executing, 5) == (None, 'completed')
        assert len(requests) == 1
        assert (await settle_reference(state.authority, row))['outcome'] == 'completed'
    finally:
        release.set()
        await asyncio.gather(executing, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_detach_waits_and_cannot_remove_replacement_subscription(resident, monkeypatch, cancel):
    from agent.reference_input_witness import ReferenceInputWitness
    from gateway.session_learning_consumer import ReferenceExecutionUnknown
    state, requests, _ = resident
    await arm(resident)
    state.live.subscribers['other'] = object()
    row, _ = await state.authority._claim_next(state.ref, state.live)
    entered, release = Event(), Event()
    original = ReferenceInputWitness.request_hook

    def paused(witness, request):
        original(witness, request)
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(ReferenceInputWitness, 'request_hook', paused)
    executing = asyncio.create_task(state.authority._execute_claimed(state.ref, state.live, row))
    detached = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        detached = asyncio.create_task(state.authority.detach(state.connection.actor, 'fixture-membership'))
        await asyncio.sleep(0)
        assert not detached.done() and state.live.subscribers['fixture-membership'] is state.connection.actor
        replacement = object()
        if cancel:
            detached.cancel()
            with pytest.raises(asyncio.CancelledError):
                await detached
            state.live.subscribers['fixture-membership'] = replacement
        release.set()
        with pytest.raises(ReferenceExecutionUnknown):
            await asyncio.wait_for(executing, 5)
        if cancel:
            with pytest.raises(runtime.RuntimeStoreError, match='permission_denied'):
                await state.authority.detach(state.connection.actor, 'fixture-membership')
            assert state.live.subscribers['fixture-membership'] is replacement
        else:
            await asyncio.wait_for(detached, 5)
            assert 'fixture-membership' not in state.live.subscribers
        assert len(requests) == 1
        assert runtime.get_session_admission(state.db, admission_id=row['admission_id'])['status'] == 'unknown'
    finally:
        release.set()
        await asyncio.gather(executing, *([detached] if detached else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_initial_admission_commit_joins_participating_route_fence(resident, monkeypatch):
    state, requests, _ = resident
    state.broker.prepare_execution(state.connection, decision_id=state.decision,
        request_id='fixture-request', conversation_history=[],
        destination='http://127.0.0.1:8000/v1', model='fixture')
    entered, release = Event(), Event()
    original = state.db._execute_write

    def paused(write, **kwargs):
        guard = kwargs.get('_commit_guard')
        if guard is not None:
            @contextmanager
            def held(conn):
                with guard(conn):
                    entered.set()
                    assert release.wait(5)
                    yield
            kwargs['_commit_guard'] = held
        return original(write, **kwargs)

    monkeypatch.setattr(state.db, '_execute_write', paused)
    admitting = asyncio.create_task(admit(state))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        with pytest.raises(runtime.RuntimeStoreError, match='reference_change_busy'):
            state.authority.fence_route(state.live.route)
        release.set()
        assert (await asyncio.wait_for(admitting, 5))['durable_admission'] is True
        assert requests == []
    finally:
        release.set()
        await asyncio.gather(admitting, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_finalizer_cannot_enqueue_private_background_review(resident, monkeypatch):
    from run_agent import AIAgent
    state, requests, parent = resident
    parent.skip_background_review = False
    spawned = []
    monkeypatch.setattr('agent.turn_finalizer._consume_skill_review_due', lambda agent: True)
    monkeypatch.setattr(AIAgent, '_spawn_background_review', lambda *a, **k: spawned.append(k))
    await arm(resident)
    row, _ = await state.authority._claim_next(state.ref, state.live)
    assert await state.authority._execute_claimed(state.ref, state.live, row) == (None, 'completed')
    assert len(requests) == 1 and spawned == []


@pytest.mark.asyncio
async def test_lost_settlement_receipt_recovers_original_commit_without_resend(resident, monkeypatch):
    from gateway.session_settlement_recovery import settle_with_retry
    state, requests, _ = resident
    await arm(resident)
    row, _ = await state.authority._claim_next(state.ref, state.live)
    await state.authority._execute_claimed(state.ref, state.live, row)
    original, lost = state.db._execute_write, []

    def lose_receipt(write, **kwargs):
        result = original(write, **kwargs)
        if isinstance(result, dict) and result.get('status') == 'terminal' and not lost:
            lost.append(True)
            raise OSError('synthetic result acknowledgment loss after real commit')
        return result

    monkeypatch.setattr(state.db, '_execute_write', lose_receipt)
    settlement = {}
    await settle_with_retry(state.authority, state.live, state.ref, row, None, 'completed',
        state.authority.pending_results[row['admission_id']], settlement)
    assert settlement['settled']['outcome'] == 'completed' and lost == [True] and len(requests) == 1
    assert settlement['response'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('selector', ['pressure', 'lru', 'idle'])
async def test_real_cache_selectors_keep_only_the_armed_parent_pinned(resident, monkeypatch, selector):
    from gateway.run_agent_cache import GatewayAgentCacheMixin
    from gateway.agent_cache_pressure import AgentCacheBounds
    from types import SimpleNamespace
    state, requests, parent = resident
    await arm(resident)
    runner = state.authority.runner
    other, released, done = SimpleNamespace(_last_activity_ts=0), [], Event()
    runner._agent_cache['other'] = (other, 'other')
    parent._last_activity_ts = 0
    runner._running_agent_ids = lambda: set()
    runner._agent_cache_bounds = lambda: AgentCacheBounds(memory_high_mb=1, protect_recent=0)
    runner._agent_cache_cap, runner._agent_cache_idle_ttl = lambda: 0, lambda: 0
    runner._commit_then_release_soft = lambda *a: None
    runner._spawn_release_thread = lambda fn, args, *a, **k: released.append(args)

    def batch(plan):
        released.extend((agent, key) for key, agent in plan)
        done.set()

    runner._release_pressure_batch = batch
    monkeypatch.setattr('gateway.agent_cache_pressure.read_anon_rss_mb', lambda: 10)
    monkeypatch.setattr('gateway.agent_cache_pressure.transcript_persistence_caught_up', lambda agent: True)
    if selector == 'pressure':
        assert GatewayAgentCacheMixin._sweep_agent_cache_under_pressure(runner) == 1
        assert await asyncio.to_thread(done.wait, 5)
    elif selector == 'lru':
        with runner._agent_cache_lock:
            GatewayAgentCacheMixin._enforce_agent_cache_cap(runner)
    else:
        assert GatewayAgentCacheMixin._sweep_idle_cached_agents(runner) == 1
    assert state.live.route in runner._agent_cache and 'other' not in runner._agent_cache
    assert released == [(other, 'other')] and requests == []


@pytest.mark.asyncio
async def test_real_owner_restart_cannot_reconstruct_executable_reference(resident):
    from gateway.session_authority import SessionAuthority, LiveSession
    from gateway.session_controls import AuthorityConnection
    state, requests, _ = resident
    receipt = await arm(resident)
    epoch = runtime.begin_runtime_epoch(state.db, instance_id='replacement-instance')
    replacement = SessionAuthority(state.authority.runner, profile_id=state.authority.profile_id,
        instance_id='replacement-instance', db=state.db, epoch=epoch)
    state.registry.remove(state.ref.profile_id)
    state.registry.add(state.ref.profile_id, replacement)
    peer = AuthorityConnection(replacement, object(), {**state.identity, 'instance_id': 'replacement-instance'})
    live = LiveSession(state.live.source, state.live.route)
    live.subscribers['new-membership'] = peer.actor
    replacement.sessions['session'] = live
    row, _ = await replacement._claim_next(state.ref, live)
    assert row is False and requests == []
    assert runtime.get_session_admission(state.db, admission_id=receipt['admission_id'])['outcome'] == 'cancelled'


@pytest.mark.asyncio
@pytest.mark.parametrize('destination', ['http://localhost:8000/v1', 'https://example.test/v1',
    'http://user@127.0.0.1:8000/v1', 'http://127.0.0.1:8000/v1?replacement=1'])
async def test_host_intent_refuses_unqualified_destination_before_admission(resident, destination):
    state, requests, _ = resident
    with pytest.raises(runtime.RuntimeStoreError):
        state.broker.prepare_execution(state.connection, decision_id=state.decision,
            request_id='fixture-request', conversation_history=[], destination=destination, model='fixture')
    assert runtime.find_session_admission(state.db, principal_id=state.connection.actor.subject,
        session_id='session', request_id='fixture-request') is None
    assert requests == []
