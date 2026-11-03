"""Non-SQL native lifetime, not note permission, input receipt or session ACK."""
import asyncio
from contextlib import contextmanager, suppress
from copy import copy
from functools import partial
from threading import Event

import pytest

from tests.gateway.test_native_transport_commit import native, visible, write
from tests.gateway.test_session_learning_admission import captured, admit
import hermes_state_runtime as runtime


def issue(state, operation='send', **kwargs):
    return state.issuer.issue_send(operation_id=operation, payload_sha256='a' * 64, **kwargs)


def test_original_send_lifetime_is_one_use_without_sql_or_content_attestation(native):
    operation = issue(native)
    assert issue(native) is operation
    with operation.hold():
        assert operation.state == 'active'
    receipt = operation.receipt()
    assert receipt['lease_released'] is True and receipt['current_transport'] is True
    assert receipt['payload_content_attested'] is False and receipt['session_acknowledged'] is False
    assert receipt['http_response_attested'] is False and receipt['transaction_commit_attested'] is False
    assert visible(native) is None
    with pytest.raises(runtime.RuntimeStoreError):
        with operation.hold():
            pytest.fail('send operation replayed')


@pytest.mark.parametrize('mutation', ['expiry', 'close', 'port', 'actor-copy'])
def test_invalid_native_send_refuses_before_body(native, monkeypatch, mutation):
    import gateway.session_native_commit as module
    clock = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    operation = issue(native, lifetime_s=1)
    mutations = {'expiry': lambda: clock.__setitem__(0, 101.0),
                 'close': native.issuer.begin_close,
                 'port': lambda: setattr(native.connection, 'transport', object()),
                 'actor-copy': lambda: setattr(native.connection, 'actor', copy(native.connection.actor))}
    mutations[mutation]()
    with pytest.raises(runtime.RuntimeStoreError):
        with operation.hold():
            pytest.fail('invalid native send entered')


def test_kind_collision_copy_and_sql_substitution_refuse(native):
    operation = issue(native)
    with pytest.raises(runtime.RuntimeStoreError):
        with copy(operation).hold():
            pytest.fail('copied send handle entered')
    with pytest.raises(runtime.RuntimeStoreError, match='admission_conflict'):
        native.connection.issue_native_commit(operation_id='send', payload_sha256='a' * 64)
    commit = native.connection.issue_native_commit(operation_id='commit', payload_sha256='a' * 64)
    with pytest.raises(runtime.RuntimeStoreError, match='admission_conflict'):
        issue(native, 'commit')
    assert commit.guard_completed is False
    with pytest.raises(runtime.RuntimeStoreError):
        native.db._execute_write(write, _commit_guard=partial(native.issuer.at_commit, operation))
    assert visible(native) is None


def test_send_body_failure_is_unknown_and_releases_native_gate(native):
    operation = issue(native)
    with pytest.raises(ValueError, match='inert failure'):
        with operation.hold():
            raise ValueError('inert failure')
    assert operation.receipt()['state'] == 'unknown'
    assert operation.receipt()['lease_released'] is True  # physical hold ended; send outcome is unknown
    assert issue(native) is operation
    with pytest.raises(runtime.RuntimeStoreError):
        with operation.hold():
            pytest.fail('unknown operation replayed')
    with issue(native, 'fresh').hold():
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_close', [False, True])
async def test_cancelled_awaiter_does_not_release_physical_worker_or_complete_close(native, monkeypatch, cancel_close):
    operation = issue(native)
    entered, release, done, closing = Event(), Event(), Event(), Event()
    begin_close = native.issuer.begin_close

    def close_started():
        begin_close()
        closing.set()

    def physical_worker():
        try:
            with operation.hold():
                entered.set()
                assert release.wait(5)
        finally:
            done.set()

    monkeypatch.setattr(native.issuer, 'begin_close', close_started)
    worker = asyncio.create_task(asyncio.to_thread(physical_worker))
    closer = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        assert operation.state == 'active' and not done.is_set()
        closer = asyncio.create_task(native.connection.close())
        assert await asyncio.to_thread(closing.wait, 5)
        assert not closer.done()
        if cancel_close:
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer
            closer = None
            assert operation.state == 'active' and not done.is_set()
        release.set()
        assert await asyncio.to_thread(done.wait, 5)
        if closer is None:
            await native.connection.close()  # same close after physical completion, no new intent
        else:
            await closer
        assert operation.receipt()['lease_released'] is True
        assert operation.receipt()['current_transport'] is False
    finally:
        release.set()
        await asyncio.to_thread(done.wait, 5)
        with suppress(asyncio.CancelledError):
            await worker
        if closer is not None:
            await closer


def test_send_and_commit_share_capacity_and_exclude_each_other(native):
    send = issue(native)
    commit = native.connection.issue_native_commit(operation_id='commit', payload_sha256='a' * 64)
    with send.hold():
        with pytest.raises(runtime.RuntimeStoreError, match='native_commit_busy'):
            native.db._execute_write(write, _commit_guard=commit.at_commit)
    assert visible(native) is None
    next_send = issue(native, 'next-send')

    @contextmanager
    def held_commit(connection):
        with commit.at_commit(connection):
            with pytest.raises(runtime.RuntimeStoreError, match='native_commit_busy'):
                with next_send.hold():
                    pytest.fail('send entered during SQL commit')
            yield

    native.db._execute_write(write, _commit_guard=held_commit)
    assert visible(native) is not None
    for number in range(61):
        issue(native, f'capacity-{number}')
    with pytest.raises(runtime.RuntimeStoreError, match='native_commit_capacity'):
        native.connection.issue_native_commit(operation_id='overflow', payload_sha256='a' * 64)
    with pytest.raises(runtime.RuntimeStoreError, match='native_commit_capacity'):
        issue(native, 'overflow-send')


@pytest.mark.asyncio
@pytest.mark.platforms('posix')
async def test_real_record_admission_transport_hold_prevents_registry_publication(captured):
    receipt = await admit(captured)
    issuer = captured.connection._native_commit_issuer
    operation = issuer.issue_send(operation_id='curated-send:' + captured.decision,
        payload_sha256=captured.metadata['payload_sha256'])
    with operation.hold():
        with pytest.raises(runtime.RuntimeStoreError, match='native_commit_busy'):
            captured.registry.remove(captured.authority.profile_id)
        assert captured.registry._by_key
    captured.registry.remove(captured.authority.profile_id)
    row = runtime.get_session_admission(captured.db, admission_id=receipt['admission_id'])
    assert row['status'] == 'queued'  # no inference, claim or consumption is fabricated
    assert operation.receipt()['current_transport'] is False
    assert operation.receipt()['session_acknowledged'] is False
