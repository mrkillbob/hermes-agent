"""Native lifetime mechanics, not redeemed tickets, note permission or distributed delivery."""
import asyncio
from contextlib import contextmanager, suppress
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from gateway.session_controls import AuthorityConnection
from gateway.session_local_migration import bind_native_transport, unbind_native_transport
from hermes_state import SessionDB
from hermes_state_commit_guard import CommitGuardFailure
import hermes_state_runtime as rt


@pytest.fixture
def native(tmp_path):
    db = SessionDB(tmp_path / 'state.db')
    authority = SimpleNamespace(profile_id=str(tmp_path / 'profile'),
        instance_id='synthetic-instance', db=db, events={}, sessions={},
        runner=SimpleNamespace(config=SimpleNamespace(multiplex_profiles=False)))
    connection = AuthorityConnection(authority, object(), {
        'provider': 'local', 'user_id': 'synthetic-user', 'native_bootstrap': True,
        'profile_id': authority.profile_id, 'instance_id': authority.instance_id})
    db.create_session('session', source='test', user_id=connection.actor.subject)
    authority.epoch = rt.begin_runtime_epoch(db, instance_id=authority.instance_id)
    try:
        yield SimpleNamespace(db=db, authority=authority, connection=connection,
                              issuer=getattr(connection, '_native_commit_issuer', None))
    finally:
        db.close()


def issue(native, operation='operation', **kwargs):
    return native.connection.issue_native_commit(operation_id=operation,
        payload_sha256='a' * 64, **kwargs)


def write(connection):
    connection.execute("INSERT INTO state_meta(key,value) VALUES('synthetic-write','value')")


def visible(native):
    with native.db._read_ctx() as connection:
        return connection.execute("SELECT value FROM state_meta WHERE key='synthetic-write'").fetchone()


def test_actual_admit_claim_settle_use_one_use_native_transport_leases(native):
    c, db, a = native.connection, native.db, native.authority
    admitted = rt.admit_session_input(db, epoch=a.epoch, principal_id=c.actor.subject,
        session_id='session', request_id='request', payload={'text': 'synthetic input'},
        _commit_guard=issue(native, 'admit').at_commit)
    claimed = rt.claim_session_input(db, epoch=a.epoch, session_id='session',
                                    _commit_guard=issue(native, 'claim').at_commit)
    operation = issue(native, 'settle')
    settled = rt.settle_session_input(db, epoch=a.epoch, admission_id=admitted['admission_id'],
        generation=claimed['generation'], outcome='completed', _commit_guard=operation.at_commit)
    assert settled['status'] == 'terminal'
    assert operation.receipt()['current_transport'] is True
    assert operation.receipt()['session_acknowledged'] is False
    assert operation.receipt()['payload_content_attested'] is False
    assert operation.receipt()['distributed_fence'] is False
    with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
        db._execute_write(write, _commit_guard=operation.at_commit)
    assert visible(native) is None


@pytest.mark.parametrize('mutation', ['port', 'actor-copy', 'registration', 'non-native', 'expiry'])
def test_changed_or_expired_native_scope_refuses_actual_write(native, monkeypatch, mutation):
    import gateway.session_native_commit as module
    clock = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    operation = issue(native, lifetime_s=1)
    mutations = {
        'port': lambda: setattr(native.connection, 'transport', object()),
        'actor-copy': lambda: setattr(native.connection, 'actor', replace(native.connection.actor)),
        'registration': native.authority._native_legacy_transports.clear,
        'non-native': lambda: setattr(native.connection, 'native_owner', False),
        'expiry': lambda: clock.__setitem__(0, 101.0),
    }
    mutations[mutation]()
    with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
        native.db._execute_write(write, _commit_guard=operation.at_commit)
    assert visible(native) is None


def test_duplicate_conflict_capacity_and_foreign_process_fail_closed(native):
    original = issue(native)
    assert issue(native) is original
    with pytest.raises(rt.RuntimeStoreError, match='admission_conflict'):
        native.connection.issue_native_commit(operation_id='operation', payload_sha256='b' * 64)
    for index in range(63):
        issue(native, f'other-{index}')
    with pytest.raises(rt.RuntimeStoreError, match='native_commit_capacity'):
        issue(native, 'overflow')
    # Wrong-process metadata must refuse BEFORE touching potentially inherited locks.
    native.issuer.process = -1
    native.issuer._state.acquire()
    try:
        with pytest.raises(rt.RuntimeStoreError, match='native_commit_process_unavailable'):
            issue(native, 'foreign')
    finally:
        native.issuer._state.release()


def test_guard_rejects_another_store_and_never_attests_payload_contents(native, tmp_path):
    other = SessionDB(tmp_path / 'other.db')
    try:
        operation = issue(native)
        with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
            other._execute_write(write, _commit_guard=operation.at_commit)
        assert operation.receipt()['payload_content_attested'] is False
    finally:
        other.close()


@pytest.mark.asyncio
async def test_close_before_guard_refuses_and_rolls_back_prepared_sql(native):
    operation = issue(native)
    prepared, release = Event(), Event()

    def delayed(connection):
        write(connection)
        prepared.set()
        assert release.wait(5)

    work = asyncio.create_task(asyncio.to_thread(native.db._execute_write, delayed,
                                               _commit_guard=operation.at_commit))
    try:
        assert await asyncio.to_thread(prepared.wait, 5)
        await native.connection.close()
        release.set()
        with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
            await work
        assert visible(native) is None
    finally:
        release.set()
        with suppress(rt.RuntimeStoreError):
            await work


@pytest.mark.asyncio
@pytest.mark.parametrize('transition', ['close', 'unbind'])
async def test_entered_commit_precedes_completed_invalidation_without_blocking_owner(native, monkeypatch, transition):
    operation = issue(native)
    entered, release = Event(), Event()
    closing_begun = asyncio.Event()
    original = native.issuer.begin_close

    def begin_close():
        original()
        closing_begun.set()

    monkeypatch.setattr(native.issuer, 'begin_close', begin_close)

    @contextmanager
    def held(connection):
        with operation.at_commit(connection):
            entered.set()
            assert release.wait(5)
            yield

    work = asyncio.create_task(asyncio.to_thread(native.db._execute_write, write, _commit_guard=held))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        if transition == 'unbind':
            with pytest.raises(rt.RuntimeStoreError, match='native_commit_busy'):
                unbind_native_transport(native.authority, native.connection.actor)
            assert native.authority._native_legacy_transports[native.connection.actor.transport_id] is native.connection.actor
        closing = asyncio.create_task(native.connection.close())
        await asyncio.wait_for(closing_begun.wait(), 5)
        assert not closing.done()
        release.set()
        await work
        await closing
        assert visible(native) is not None
        assert operation.receipt()['commit_guard_completed'] is True
        assert operation.receipt()['current_transport'] is False
        assert operation.receipt()['session_acknowledged'] is False
        assert native.authority.events == {}
        assert native.authority._native_commit_issuers == {}
    finally:
        release.set()
        await work
        if closing is not None:
            await closing


def test_post_commit_failure_is_unknown_and_never_mints_another_operation(native):
    operation = issue(native)

    @contextmanager
    def uncertain(connection):
        with operation.at_commit(connection):
            yield
            raise ValueError('synthetic post-commit uncertainty')

    with pytest.raises(CommitGuardFailure):
        native.db._execute_write(write, _commit_guard=uncertain)
    assert visible(native) is not None
    assert operation.receipt()['state'] == 'unknown'
    assert issue(native) is operation
    with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
        native.db._execute_write(lambda connection: None, _commit_guard=operation.at_commit)


def test_original_objects_remain_pinned_even_if_current_maps_are_rebound(native):
    operation = issue(native)
    old = native.connection.actor
    replacement = replace(old)
    native.connection.actor = replacement
    native.authority._native_legacy_transports[old.transport_id] = replacement
    native.connection.transport = object()
    native.authority.events[old.transport_id] = native.connection.transport
    with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
        native.db._execute_write(write, _commit_guard=operation.at_commit)
    assert visible(native) is None


def test_direct_guard_cannot_report_a_commit_that_it_did_not_observe(native):
    operation = issue(native)
    conn = native.db._conn
    with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
        with operation.at_commit(conn):
            pass
    assert operation.receipt()['commit_guard_completed'] is False
    conn.execute('BEGIN IMMEDIATE')
    try:
        with pytest.raises(rt.RuntimeStoreError, match='native_commit_transaction_open'):
            with operation.at_commit(conn):
                pass
    finally:
        conn.rollback()
    assert operation.receipt()['state'] == 'unknown'
    assert operation.receipt()['commit_guard_completed'] is False
    other = issue(native, 'rollback-is-not-commit-proof')
    conn.execute('BEGIN IMMEDIATE')
    with other.at_commit(conn):
        conn.rollback()
    assert other.receipt()['commit_guard_completed'] is True
    assert other.receipt()['transaction_commit_attested'] is False
    assert 'commit_returned' not in other.receipt()


def test_registered_rebind_retires_old_issuer_without_minting_new_authority(native):
    operation = issue(native)
    bind_native_transport(native.authority, native.connection.actor, native.connection._identity)
    assert native.issuer.closing
    assert native.authority._native_legacy_transports[native.connection.actor.transport_id] is native.connection.actor
    with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
        native.db._execute_write(write, _commit_guard=operation.at_commit)
    with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
        issue(native, 'replacement')
    assert visible(native) is None


@pytest.mark.asyncio
async def test_failed_teardown_keeps_seal_and_allows_later_cleanup(native):
    subscription = object()
    live = SimpleNamespace(subscribers=[subscription])
    native.connection.subscriptions['session'] = subscription
    native.authority.sessions['session'] = live
    attempts = []

    async def detach(actor, selected, **kwargs):
        attempts.append(selected)
        if len(attempts) == 1:
            raise ValueError('synthetic cleanup failure')
        live.subscribers.remove(selected)

    native.authority.detach = detach
    with pytest.raises(ValueError, match='synthetic cleanup failure'):
        await native.connection.close()
    assert native.issuer.closing
    with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
        issue(native)
    await native.connection.close()
    await native.connection.close()
    assert len(attempts) == 2
    assert native.authority.events == {}
    assert native.authority._native_commit_issuers == {}


@pytest.mark.asyncio
async def test_cancelled_close_does_not_reopen_intake_and_can_finish_later(native, monkeypatch):
    operation = issue(native)
    entered, release = Event(), Event()
    closing_begun = asyncio.Event()
    original = native.issuer.begin_close

    def begin_close():
        original()
        closing_begun.set()

    monkeypatch.setattr(native.issuer, 'begin_close', begin_close)

    @contextmanager
    def held(connection):
        with operation.at_commit(connection):
            entered.set()
            assert release.wait(5)
            yield

    work = asyncio.create_task(asyncio.to_thread(native.db._execute_write, write, _commit_guard=held))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        with pytest.raises(rt.RuntimeStoreError, match='native_commit_busy'):
            bind_native_transport(native.authority, native.connection.actor, native.connection._identity)
        closing = asyncio.create_task(native.connection.close())
        await asyncio.wait_for(closing_begun.wait(), 5)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert native.issuer.closing
        with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
            issue(native, 'after-cancel')
        release.set()
        await work
        await native.connection.close()
        assert operation.receipt()['current_transport'] is False
        assert native.authority.events == {}
    finally:
        release.set()
        await work
        if closing is not None and not closing.done():
            closing.cancel()
            with suppress(asyncio.CancelledError):
                await closing


@pytest.mark.asyncio
async def test_sibling_replacement_refuses_busy_old_port_then_closes_whole_tree(native, monkeypatch):
    # Only profile-name resolution is synthetic; constructor, replacement and close
    # are real methods. This does not qualify profile-registry authorization.
    monkeypatch.setattr('hermes_cli.profiles.profile_matches_home',
                        lambda selected, home: selected == str(home))
    root = native.connection
    root._identity['profile_scope'] = 'host'

    def authority(profile, instance):
        return SimpleNamespace(profile_id=profile, instance_id=instance, db=native.db,
            events={}, sessions={}, runner=native.authority.runner)

    child_authority = authority(native.authority.profile_id + '-child', 'child-one')
    native.authority.runner.session_authorities = [child_authority]
    child = root._sibling_for(child_authority.profile_id)
    operation = child.issue_native_commit(operation_id='old-child', payload_sha256='a' * 64)
    entered, release = Event(), Event()

    @contextmanager
    def held(connection):
        with operation.at_commit(connection):
            entered.set()
            assert release.wait(5)
            yield

    work = asyncio.create_task(asyncio.to_thread(native.db._execute_write, write, _commit_guard=held))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        replacement = authority(child_authority.profile_id, 'child-two')
        native.authority.runner.session_authorities = [replacement]
        with pytest.raises(rt.RuntimeStoreError, match='native_commit_busy'):
            root._sibling_for(replacement.profile_id)
        assert root._siblings[replacement.profile_id] is child
        assert child._native_commit_issuer.closing
        release.set()
        await work
        fresh = root._sibling_for(replacement.profile_id)
        assert fresh is not child and fresh.authority is replacement
        assert operation.receipt()['current_transport'] is False
        grand_authority = authority(replacement.profile_id + '-grand', 'grand')
        native.authority.runner.session_authorities = [replacement, grand_authority]
        grand = fresh._sibling_for(grand_authority.profile_id)
        await root.close()
        for connection in (root, fresh, grand):
            assert connection._native_commit_issuer.closing
            assert connection.authority.events == {}
            with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
                connection.issue_native_commit(operation_id='closed', payload_sha256='a' * 64)
        with pytest.raises(rt.RuntimeStoreError, match='native_commit_unavailable'):
            root._sibling_for(replacement.profile_id)
    finally:
        release.set()
        await work
