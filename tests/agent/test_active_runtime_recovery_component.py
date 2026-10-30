"""AUTHOR-ONLY offline candidate: real voice/reinstall/reservation/switch control flow.

No full AIAgent, preferred-provider recovery, live client, or external-call claim.
Do not execute before root's containment review.
"""

from threading import Event, Thread
import copy
import logging

import pytest

from tests.agent.startup_runtime_component_support import real_components, PRIMARY_URL, FALLBACK_URL

from tests.agent.active_runtime_test_support import (
    FactoryFailure, active_component, begin_voice, select_model, assert_fallback_restored,
    prepare_resolved_primary,
)

# Explicitly export imported fixtures used by pytest name-based injection.
__all__ = ['active_component', 'real_components']


def test_normal_voice_teardown_restores_active_fallback_without_fabricating_primary(active_component):
    h = active_component
    record = begin_voice(h)
    assert record.state['snapshot']['model'] == 'fallback-resolved-model'
    assert h.voice.end_voice_turn_route(h.agent) is None
    assert_fallback_restored(h)
    assert h.agent._voice_route_state is None
    assert h.mutation.runtime_controller(h.agent).voice_snapshot()[:2] == (None, None)
    assert len(h.factory_attempts) == len(h.revalidations) == 1
    assert h.voice.end_voice_turn_route(h.agent) is None
    assert len(h.factory_attempts) == len(h.revalidations) == 1


def test_failed_voice_factory_retries_exact_backup_once_without_primary_notice(active_component, caplog):
    h = active_component
    caplog.set_level(logging.INFO)
    record = begin_voice(h)
    state = record.state
    h.fail_factory = True
    assert h.voice.end_voice_turn_route(h.agent) == 'deferred'
    current, debt, *_ = h.mutation.runtime_controller(h.agent).voice_snapshot()
    assert current is debt is record
    assert record.state is state
    assert h.revalidations == []
    assert h.voice.end_voice_turn_route(h.agent) == 'deferred'
    assert len(h.factory_attempts) == 1
    h.fail_factory = False
    assert h.snapshot.restore_primary_runtime(h.agent) is False
    assert_fallback_restored(h)
    assert h.mutation.runtime_controller(h.agent).voice_snapshot()[:2] == (None, None)
    assert len(h.factory_attempts) == 2
    assert len(h.revalidations) == 1
    assert h.snapshot.restore_primary_runtime(h.agent) is False
    assert len(h.factory_attempts) == 2
    assert not any('Primary runtime restored' in record.message for record in caplog.records)


def test_repeated_failed_recovery_keeps_first_backup_and_blocks_next_turn(active_component):
    h = active_component
    record = begin_voice(h)
    h.fail_factory = True
    assert h.voice.end_voice_turn_route(h.agent) == 'deferred'
    assert h.voice.end_voice_turn_route(h.agent) == 'deferred'
    assert len(h.factory_attempts) == 1
    for _ in range(2):
        with pytest.raises(h.snapshot.RuntimeRecoveryPendingError):
            h.snapshot.restore_primary_runtime(h.agent)
        current, debt, *_ = h.mutation.runtime_controller(h.agent).voice_snapshot()
        assert current is debt is record
        assert h.agent._primary_runtime is None
    assert len(h.factory_attempts) == 3
    assert h.revalidations == []
    h.fail_factory = False
    assert h.snapshot.restore_primary_runtime(h.agent) is False
    assert_fallback_restored(h)
    assert len(h.revalidations) == 1


@pytest.mark.parametrize('switch_fails', [False, True], ids=['selection-wins', 'failed-selection-retains-debt'])
def test_busy_voice_teardown_during_actual_switch_obeys_success_boundary(active_component, switch_fails):
    h = active_component
    record = begin_voice(h)
    controller = h.mutation.runtime_controller(h.agent)
    revision, selected = controller.voice_snapshot()[2:4]
    h.fail_switch = switch_fails
    deferred = []

    def end_during_switch():
        deferred.append(h.voice.end_voice_turn_route(h.agent))
        assert controller.voice_snapshot()[1] is record

    h.switch_callback = end_during_switch
    if switch_fails:
        with pytest.raises(FactoryFailure):
            select_model(h)
        # The actual helper's outer rollback restores its route/client fields.
        assert h.agent.model == 'voice-model'
        assert h.agent.api_key == 'synthetic-voice'
        assert h.agent._primary_runtime is None
        assert controller.voice_snapshot()[1] is record
        assert controller.voice_snapshot()[2:4] == (revision, selected)
        assert h.snapshot.restore_primary_runtime(h.agent) is False
        assert_fallback_restored(h)
        assert len(h.factory_attempts) == len(h.revalidations) == 1
    else:
        select_model(h)
        assert h.agent.model == 'selected-model'
        assert h.agent.client is h.selected_client
        assert h.agent._primary_runtime['model'] == 'selected-model'
        assert h.agent._fallback_activated is False
        assert controller.voice_snapshot()[:2] == (None, None)
        assert controller.voice_snapshot()[2] == revision + 1
        assert controller.voice_snapshot()[3] != selected
        assert h.agent._voice_route_state is None
        # Late finalization cannot reinstall or restore old reasoning.
        reasoning = h.agent.reasoning_config
        assert h.voice.end_voice_turn_route(h.agent) is None
        assert h.agent.model == 'selected-model'
        assert h.agent.reasoning_config is reasoning
        assert h.factory_attempts == h.revalidations == []
    assert deferred == ['deferred']
    assert h.switch_attempts == [('selected-model', 'openai')]


def test_inflight_restore_rejects_competing_actual_switch_before_destination_mutation(active_component):
    h = active_component
    begin_voice(h)
    h.fail_factory = True
    assert h.voice.end_voice_turn_route(h.agent) == 'deferred'
    h.fail_factory = False
    entered, release = Event(), Event()
    outcome, errors = [], []

    def wait_in_factory():
        entered.set()
        assert release.wait(5), 'test did not release synthetic factory'

    def recover():
        try:
            outcome.append(h.snapshot.restore_primary_runtime(h.agent))
        except BaseException as exc:
            errors.append(exc)

    h.factory_callback = wait_in_factory
    thread = Thread(target=recover, daemon=True)
    thread.start()
    try:
        assert entered.wait(5)
        before = (h.agent.model, h.agent.provider, h.agent.api_key, h.agent.client,
                  h.agent._primary_runtime, h.agent.reasoning_config)
        with pytest.raises(h.mutation.RuntimeMutationBusyError):
            select_model(h)
        assert h.switch_attempts == []
        assert (h.agent.model, h.agent.provider, h.agent.api_key, h.agent.client,
                h.agent._primary_runtime, h.agent.reasoning_config) == before
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert errors == []
    assert outcome == [False]
    assert_fallback_restored(h)
    assert len(h.revalidations) == 1


def test_factory_callback_reentry_is_rejected_without_bookkeeping_lock_or_deadlock(active_component):
    h = active_component
    begin_voice(h)
    observations = []

    def reenter():
        controller = h.mutation.runtime_controller(h.agent)
        acquired = controller.mutex.acquire(blocking=False)
        assert acquired
        controller.mutex.release()
        with pytest.raises(h.mutation.RuntimeMutationBusyError):
            select_model(h)
        with pytest.raises(h.mutation.RuntimeMutationBusyError):
            h.snapshot.restore_primary_runtime(h.agent)
        observations.append('rejected')

    h.factory_callback = reenter
    assert h.voice.end_voice_turn_route(h.agent) is None
    assert observations == ['rejected']
    assert h.switch_attempts == []
    assert_fallback_restored(h)
    assert len(h.factory_attempts) == len(h.revalidations) == 1


def test_resolved_primary_retains_real_reinstall_and_fallback_reset_behavior(
    active_component, real_components, monkeypatch, caplog,
):
    h = active_component
    caplog.set_level(logging.INFO)
    runtime = prepare_resolved_primary(h, real_components, monkeypatch)
    assert h.snapshot.restore_primary_runtime(h.agent) is True
    a = h.agent
    assert a._primary_runtime is runtime
    assert (a.model, a.provider, a.requested_provider, a.base_url, a.api_key) == (
        'primary-model', 'openai', 'openai', PRIMARY_URL, 'synthetic-primary',
    )
    assert a._client_kwargs == {
        'api_key': 'synthetic-primary', 'base_url': PRIMARY_URL, 'timeout': 10,
    }
    assert a.client is h.rebuilt_client
    assert (a._fallback_activated, a._fallback_index, a._rate_limit_backoff_count) == (False, 0, 0)
    assert a._provider_fallback_active is False
    assert a._provider_fallback_route is None
    cc = a.context_compressor
    assert (cc.model, cc.provider, cc.base_url, cc.api_key, cc.context_length) == (
        'primary-model', 'openai', PRIMARY_URL, 'synthetic-primary', 8192,
    )
    assert h.gate_calls == ['entitlement', 'catalog', 'quota', 'reset']
    assert h.pool_rebinds == [('openai', 'primary-model')]
    assert len(h.factory_attempts) == len(h.revalidations) == 1
    assert h.revalidations == [('primary-model', PRIMARY_URL)]
    assert any('Primary runtime restored for new turn' in item.message for item in caplog.records)
    assert len(h.diagnostics) == 1 and 'Primary model restored' in h.diagnostics[0]


@pytest.mark.parametrize('block_kind,expected_gates', [
    ('entitlement', ['entitlement']),
    ('non-chat', ['entitlement', 'catalog']),
    ('cooldown', ['entitlement', 'catalog', 'quota']),
    ('reset', ['entitlement', 'catalog', 'quota', 'reset']),
])
def test_resolved_primary_rejection_gates_keep_active_fallback_intact(
    active_component, real_components, monkeypatch, block_kind, expected_gates,
):
    h = active_component
    runtime = prepare_resolved_primary(h, real_components, monkeypatch)
    h.block_kind = block_kind
    if block_kind == 'cooldown':
        h.agent._rate_limited_until = h.helpers.time.monotonic() + 600
    before = dict(vars(h.agent))
    nested_before = copy.deepcopy({key: getattr(h.agent, key) for key in
        ('_client_kwargs', 'request_overrides', 'reasoning_config', '_primary_runtime')})
    compressor_before = copy.deepcopy(vars(h.agent.context_compressor))
    assert h.snapshot.restore_primary_runtime(h.agent) is False
    assert h.agent._primary_runtime is runtime
    assert (h.agent.model, h.agent.provider, h.agent.base_url, h.agent.api_key) == (
        'fallback-resolved-model', 'openrouter', FALLBACK_URL, 'synthetic-fallback',
    )
    assert {key: value for key, value in vars(h.agent).items() if key != '_runtime_mutation_state'} == {
        key: value for key, value in before.items() if key != '_runtime_mutation_state'
    }
    assert {key: getattr(h.agent, key) for key in nested_before} == nested_before
    assert vars(h.agent.context_compressor) == compressor_before
    assert h.gate_calls == expected_gates
    assert h.factory_attempts == h.revalidations == h.pool_rebinds == h.diagnostics == []
