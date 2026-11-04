"""Retirement regressions for original lifetimes, cleanup and ordinary peers."""
import asyncio
from dataclasses import replace
from threading import Event

import pytest

from tests.gateway import test_reference_consumed_input as fixtures
import hermes_state_runtime as runtime

resident, captured, bridge = fixtures.resident, fixtures.captured, fixtures.bridge
arm = fixtures.arm
pytestmark = pytest.mark.platforms('posix')


@pytest.mark.asyncio
async def test_cancelled_awaiter_keeps_sync_mutations_fenced_until_actual_worker_exit(resident, monkeypatch):
    from agent.reference_input_witness import ReferenceInputWitness
    from gateway.session_runtime_workers import mutation_tasks
    state, _, _ = resident
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
        executing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await executing
        with pytest.raises(runtime.RuntimeStoreError, match='reference_change_busy'):
            state.authority.fence_route(state.live.route)
    finally:
        release.set()
        await asyncio.gather(executing, *list(mutation_tasks(state.authority)), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('retirement', ['revoke', 'expiry', 'prune'])
async def test_abandoned_preparation_releases_parent_history_and_intent(resident, retirement):
    state, requests, parent = resident
    state.broker.prepare_execution(state.connection, decision_id=state.decision,
        request_id='fixture-request', conversation_history=[{'role': 'user', 'content': 'INERT-HOST-HISTORY'}],
        destination='http://127.0.0.1:8000/v1', model='fixture')
    selected = state.broker._decisions[state.decision]
    execution = selected.execution
    assert any(pinned is parent for pinned in state.authority.runner._reference_parent_pins.values())
    if retirement == 'revoke':
        state.broker.revoke(state.connection, decision_id=state.decision, request_id='fixture-request')
    else:
        selected.expires_at = 0
        if retirement == 'expiry':
            with pytest.raises(runtime.RuntimeStoreError):
                state.broker._selected(state.connection, state.decision, 'fixture-request')
        else:
            state.broker.select(state.connection, state.ref, request_id='next-request', exact=selected.exact)
    assert state.authority.runner._reference_parent_pins == {}
    assert execution.history == [] and execution.parent is None
    assert state.authority._reference_intents == {} and requests == []


@pytest.mark.asyncio
async def test_ordinary_value_equal_principal_keeps_existing_detach_contract(resident):
    state, _, _ = resident
    peer = replace(state.connection.actor)
    assert peer == state.connection.actor and peer is not state.connection.actor
    state.live.subscribers['other'] = object()
    await state.authority.detach(peer, 'fixture-membership')
    assert 'fixture-membership' not in state.live.subscribers


@pytest.mark.asyncio
async def test_actual_sdk_close_failure_cannot_be_a_successful_consumption_result(resident, monkeypatch, caplog):
    from openai import OpenAI
    from gateway.session_learning_consumer import ReferenceExecutionUnknown
    state, requests, _ = resident
    await arm(resident)
    row, _ = await state.authority._claim_next(state.ref, state.live)
    execution = state.authority._reference_executions[row['admission_id']]
    original, failed = OpenAI.close, []

    def strict_failure(client):
        witness = execution.witness
        if witness is not None and client._client is witness.transport:
            assert execution.native_send.state == 'active' and execution.worker_gate.locked()
            failed.append(True)
            raise RuntimeError('PRIVATE-SYNTHETIC-REFERENCE actual SDK close failed')
        return original(client)

    monkeypatch.setattr(OpenAI, 'close', strict_failure)
    with pytest.raises(ReferenceExecutionUnknown):
        await state.authority._execute_claimed(state.ref, state.live, row)
    assert failed and len(requests) == 1
    assert execution.native_send.receipt()['state'] == 'unknown'
    assert runtime.get_session_admission(state.db, admission_id=row['admission_id'])['status'] == 'unknown'
    assert 'PRIVATE-SYNTHETIC-REFERENCE' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('transition', ['close', 'owner_removal'])
async def test_native_lifecycle_retires_prepared_but_unadmitted_intent(resident, transition):
    state, requests, _ = resident
    state.broker.prepare_execution(state.connection, decision_id=state.decision,
        request_id='fixture-request', conversation_history=[],
        destination='http://127.0.0.1:8000/v1', model='fixture')
    if transition == 'close':
        await state.connection.close()
    else:
        state.registry.remove(state.ref.profile_id)
    assert state.authority.runner._reference_parent_pins == {}
    assert state.authority._reference_intents == {} and requests == []
