"""Pure reservation contracts: no agent construction, providers or runtime factories."""

from contextvars import copy_context
from threading import Barrier, Event, Thread
from types import SimpleNamespace

import pytest

from agent.runtime_mutation import (
    RuntimeMutationBusyError, get_runtime_mutation_state, runtime_mutation,
)


def test_same_thread_reentry_refuses_before_body_and_releases():
    agent = SimpleNamespace(value="old")
    with runtime_mutation(agent, "switch_model"):
        with pytest.raises(RuntimeMutationBusyError), runtime_mutation(agent, "switch_model"):
            agent.value = "wrong"
        assert agent.value == "old"
    with runtime_mutation(agent, "switch_model"):
        agent.value = "new"
    assert agent.value == "new"


def test_callbacks_run_without_bookkeeping_lock():
    agent = SimpleNamespace()
    with runtime_mutation(agent, "switch_model") as lease:
        assert lease.controller.mutex.acquire(blocking=False)
        lease.controller.mutex.release()


def test_competing_thread_refuses_while_owner_callback_is_waiting():
    agent = SimpleNamespace(value="old")
    entered, finish, refused = Event(), Event(), Event()
    errors = []

    def owner():
        try:
            with runtime_mutation(agent, "switch_model"):
                entered.set()
                assert finish.wait(3)
        except BaseException as exc:
            errors.append(exc)

    def contender():
        try:
            with runtime_mutation(agent, "restore_primary_runtime"):
                agent.value = "wrong"
        except RuntimeMutationBusyError:
            refused.set()

    first = Thread(target=owner)
    second = Thread(target=contender)
    first.start()
    try:
        assert entered.wait(3)
        second.start()
        assert refused.wait(3)
        assert agent.value == "old"
    finally:
        finish.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []


def test_internal_composition_consumes_permit_before_callback():
    agent = SimpleNamespace()
    seen = []

    def internal():
        with runtime_mutation(agent, "restore_primary_runtime") as nested:
            seen.append(nested.token)
            with pytest.raises(RuntimeMutationBusyError), runtime_mutation(agent, "restore_primary_runtime"):
                pytest.fail("public callback entered")

    with runtime_mutation(agent, "one_turn_restore") as lease:
        lease.call("restore_primary_runtime", internal)
        assert seen == [lease.token]
        with pytest.raises(RuntimeMutationBusyError), runtime_mutation(agent, "restore_primary_runtime"):
            pytest.fail("permit leaked")


def test_permit_operation_and_agent_must_match():
    agent, other = SimpleNamespace(), SimpleNamespace()
    with runtime_mutation(agent, "switch_model") as lease:
        def wrong_operation():
            with runtime_mutation(agent, "begin_voice_turn_route"):
                pytest.fail("wrong operation entered")
        with pytest.raises(RuntimeMutationBusyError):
            lease.call("restore_primary_runtime", wrong_operation)
        with runtime_mutation(other, "restore_primary_runtime"):
            def wrong_agent():
                with runtime_mutation(other, "restore_primary_runtime"):
                    pytest.fail("wrong agent entered")
            with pytest.raises(RuntimeMutationBusyError):
                lease.call("restore_primary_runtime", wrong_agent)


def test_copied_permit_cannot_authorize_another_thread():
    agent = SimpleNamespace()
    errors = []
    with runtime_mutation(agent, "switch_model") as lease:
        def attempt():
            try:
                with runtime_mutation(agent, "restore_primary_runtime"):
                    errors.append("entered")
            except RuntimeMutationBusyError:
                errors.append("refused")

        def copy_to_thread():
            context = copy_context()
            thread = Thread(target=lambda: context.run(attempt))
            thread.start()
            thread.join(3)
            assert not thread.is_alive()

        lease.call("restore_primary_runtime", copy_to_thread)
    assert errors == ["refused"]


def test_unused_copied_permit_is_revoked_on_call_return():
    agent = SimpleNamespace()
    with runtime_mutation(agent, "switch_model") as lease:
        context = lease.call("restore_primary_runtime", copy_context)
        def attempt():
            with runtime_mutation(agent, "restore_primary_runtime"):
                pytest.fail("expired copied permit entered")
        with pytest.raises(RuntimeMutationBusyError):
            context.run(attempt)


def test_exception_releases_and_composition_context_does_not_leak():
    class Stop(BaseException):
        pass

    agent = SimpleNamespace()
    def fail():
        with runtime_mutation(agent, "restore_primary_runtime"):
            raise Stop
    with pytest.raises(Stop), runtime_mutation(agent, "switch_model") as lease:
        lease.call("restore_primary_runtime", fail)
    with runtime_mutation(agent, "end_voice_turn_route"):
        pass
    with pytest.raises(RuntimeMutationBusyError):
        lease.call("restore_primary_runtime", lambda: pytest.fail("expired lease called"))


def test_successful_selection_invalidates_only_after_explicit_commit():
    agent = SimpleNamespace()
    state = get_runtime_mutation_state(agent)
    old_voice, old_debt = object(), object()
    with state.mutex:
        state.voice_lease, state.cleanup_debt = old_voice, old_debt
        token = state.selection_token
    with runtime_mutation(agent, "switch_model") as lease:
        assert state.revision == 0 and state.selection_token is token
        assert lease.successful_selection() is old_voice
    with state.mutex:
        assert state.revision == 1 and state.selection_token is not token
        assert state.voice_lease is None and state.cleanup_debt is None
    with pytest.raises(RuntimeMutationBusyError):
        lease.successful_selection()


def test_failed_selection_preserves_revision_and_recovery_debt():
    agent = SimpleNamespace()
    state = get_runtime_mutation_state(agent)
    voice, debt, token = object(), object(), state.selection_token
    with state.mutex:
        state.voice_lease, state.cleanup_debt = voice, debt
    with pytest.raises(ValueError), runtime_mutation(agent, "switch_model"):
        raise ValueError("synthetic failure")
    with state.mutex:
        assert state.revision == 0 and state.selection_token is token
        assert state.voice_lease is voice and state.cleanup_debt is debt


def test_lazy_controller_creation_is_race_safe():
    agent = SimpleNamespace()
    barrier = Barrier(5)
    states = []
    def obtain():
        barrier.wait(timeout=3)
        states.append(get_runtime_mutation_state(agent))
    threads = [Thread(target=obtain) for _ in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=3)
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert len(states) == 4 and all(state is states[0] for state in states)


def test_unknown_operation_refuses_before_creating_state():
    agent = SimpleNamespace()
    with pytest.raises(ValueError), runtime_mutation(agent, "global_runtime"):
        pytest.fail("unknown operation entered")
    assert not hasattr(agent, "_runtime_mutation_state")


def test_atomic_voice_metadata_uses_record_identity_and_current_registration():
    agent = SimpleNamespace()
    state = get_runtime_mutation_state(agent)
    old, current = object(), object()
    with runtime_mutation(agent, "begin_voice_turn_route") as lease:
        state.register_voice(lease, old)
        voice, debt, revision, token, busy = state.voice_snapshot()
        assert voice is old and debt is None and revision == 0 and busy
        assert token is state.selection_token
        assert state.defer_voice(old)
        state.register_voice(lease, old)
        assert state.voice_snapshot()[:2] == (old, old)
        with pytest.raises(RuntimeMutationBusyError):
            state.register_voice(lease, current)
        assert state.voice_snapshot()[:2] == (old, old)
        assert state.finish_voice(old)
        state.register_voice(lease, current)
        assert not state.defer_voice(old)
        assert not state.finish_voice(old)
        assert state.voice_snapshot()[:2] == (current, None)
        assert state.defer_voice(current)
        assert state.finish_voice(current)
        assert state.voice_snapshot()[:2] == (None, None)
    assert not state.busy
    with pytest.raises(RuntimeMutationBusyError):
        state.register_voice(lease, old)
    with pytest.raises(RuntimeMutationBusyError):
        state.record_successful_selection()


def test_voice_registration_refuses_another_agents_lease():
    agent, other = SimpleNamespace(), SimpleNamespace()
    state = get_runtime_mutation_state(agent)
    with runtime_mutation(other, "begin_voice_turn_route") as lease:
        with pytest.raises(RuntimeMutationBusyError):
            state.register_voice(lease, object())
    assert state.voice_snapshot()[:2] == (None, None)


def test_cleanup_requested_before_registration_binds_exact_record_and_selection_clears_intent():
    agent = SimpleNamespace()
    state = get_runtime_mutation_state(agent)
    record = object()
    with runtime_mutation(agent, "begin_voice_turn_route") as lease:
        assert state.request_voice_cleanup()
        state.register_voice(lease, record)
        assert state.voice_snapshot()[:2] == (record, record)
        assert not state.cleanup_requested
        state.finish_voice(record)
        assert state.request_voice_cleanup()
        lease.successful_selection()
        assert not state.cleanup_requested
        state.register_voice(lease, object())
        assert state.voice_snapshot()[1] is None
