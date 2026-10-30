"""Real temporary-owner boundaries for implicit resume hints; no provider execution."""
from contextlib import asynccontextmanager, closing
import asyncio
import json
import sqlite3

import pytest

from tests.gateway.test_resume_latest_local_cli import _owner, _call, _reset
from tests.gateway.breadcrumb_test_support import (
    _fence_unrelated_effects, _preflight, _absent,
)


@asynccontextmanager
async def isolated_owner(tmp_path, monkeypatch):
    attempted = _fence_unrelated_effects(monkeypatch)
    async def no_inventory(*args, **kwargs):
        from gateway.session_contract import CANONICAL_GATEWAY_PROTOCOL
        return {'desktop_protocol': CANONICAL_GATEWAY_PROTOCOL}
    monkeypatch.setattr('gateway.session_local_inventory.snapshot_info', no_inventory)
    store, authority, owner = _owner(tmp_path / 'home', monkeypatch)
    try:
        yield store, authority, owner
    finally:
        try:
            await owner.close()
        finally:
            store.close_all_db_handles()
        assert attempted == []
        _absent(_preflight())


async def create(store, authority, owner, tmp_path, name):
    result = await _call(owner, 'session.create', request_id=name, source='cli',
                         cwd=str(tmp_path), toolsets=[], model='m')
    sid = result['session_id']
    authority.db.create_session(sid, source='cli')
    live = authority.sessions[sid]
    store._record_gateway_session_peer(sid, live.route, live.source)
    return sid


def admission(db, sid, status, name='synthetic-admission'):
    with db._lock:
        db._conn.execute(
            'INSERT INTO session_admissions(admission_id,request_id,principal_id,target_session_id,'
            'lineage_json,payload_json,payload_digest,intent,status) VALUES(?,?,?,?,?,?,?,?,?)',
            (name, name, 'human', sid, json.dumps([sid]), '{}', 'd', 'queue', status))
        db._conn.commit()


@pytest.mark.asyncio
async def test_explicit_empty_id_is_still_resumable(tmp_path, monkeypatch):
    async with isolated_owner(tmp_path, monkeypatch) as (store, authority, owner):
        sid = await create(store, authority, owner, tmp_path, 'explicit')
        result = await _call(owner, 'session.resume', session_id=sid)
        assert result['session_id'] == sid


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [0, 1, 'true', None])
async def test_raw_nonboolean_flag_never_reaches_eligibility(tmp_path, monkeypatch, bad):
    async with isolated_owner(tmp_path, monkeypatch) as (store, authority, owner):
        sid = await create(store, authority, owner, tmp_path, 'invalid-flag')
        def forbidden(*args, **kwargs):
            pytest.fail('invalid flag reached eligibility')
        monkeypatch.setattr('gateway.session_local_title.eligible_breadcrumb', forbidden)
        assert await _call(owner, 'session.resume', session_id=sid, breadcrumb_candidate=bad) == 'invalid_params'


@pytest.mark.asyncio
@pytest.mark.parametrize('selector', ['latest', 'title'])
async def test_flag_rejects_conflicting_selectors(tmp_path, monkeypatch, selector):
    async with isolated_owner(tmp_path, monkeypatch) as (store, authority, owner):
        sid = await create(store, authority, owner, tmp_path, 'selector')
        assert await _call(owner, 'session.resume', session_id=sid, breadcrumb_candidate=True,
                           **{selector: 'cli'}) == 'invalid_params'


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [None, 'queued', 'unknown', 'terminal'])
async def test_real_reset_excludes_old_messages_but_preserves_logical_admissions(
        tmp_path, monkeypatch, status):
    async with isolated_owner(tmp_path, monkeypatch) as (store, authority, owner):
        sid = await create(store, authority, owner, tmp_path, 'reset')
        authority.db.append_message(sid, 'user', 'synthetic old conversation')
        if status is not None and status != 'unknown':
            admission(authority.db, sid, status)
        _reset(authority, store, sid, 'synthetic-reset-child')
        if status == 'unknown':
            admission(authority.db, sid, status)
        from gateway.session_local_title import eligible_breadcrumb
        assert eligible_breadcrumb(authority, sid) is (status is not None)


@pytest.mark.asyncio
async def test_current_physical_admission_preserves_reset_hint(tmp_path, monkeypatch):
    async with isolated_owner(tmp_path, monkeypatch) as (store, authority, owner):
        sid = await create(store, authority, owner, tmp_path, 'physical')
        child = _reset(authority, store, sid, 'synthetic-physical-child')
        admission(authority.db, child, 'terminal')
        from gateway.session_local_title import eligible_breadcrumb
        assert eligible_breadcrumb(authority, sid) is True


@pytest.mark.asyncio
async def test_authorization_precedes_eligibility(tmp_path, monkeypatch):
    async with isolated_owner(tmp_path, monkeypatch) as (store, authority, owner):
        sid = await create(store, authority, owner, tmp_path, 'auth')
        from hermes_state_runtime import RuntimeStoreError
        def deny(*args, **kwargs):
            raise RuntimeStoreError('permission_denied')
        def forbidden(*args, **kwargs):
            pytest.fail('unauthorized request reached eligibility')
        monkeypatch.setattr(authority, 'authorize', deny)
        monkeypatch.setattr('gateway.session_local_title.eligible_breadcrumb', forbidden)
        result = await _call(owner, 'session.resume', session_id=sid, breadcrumb_candidate=True)
        assert result in ('permission_denied', 'not_found')


@pytest.mark.asyncio
async def test_rejected_hint_retains_cursor_accepted_hint_consumes_it(tmp_path, monkeypatch):
    async with isolated_owner(tmp_path, monkeypatch) as (store, authority, owner):
        sid = await create(store, authority, owner, tmp_path, 'cursor')
        owner.replay_cursors[sid] = 0
        assert await _call(owner, 'session.resume', session_id=sid, breadcrumb_candidate=True) == 'not_found'
        assert owner.replay_cursors[sid] == 0
        authority.db.append_message(sid, 'user', 'synthetic accepted conversation')
        result = await _call(owner, 'session.resume', session_id=sid, breadcrumb_candidate=True)
        assert result['session_id'] == sid
        assert sid not in owner.replay_cursors


@pytest.mark.asyncio
@pytest.mark.parametrize('implicit', [False, True])
async def test_cursor_published_during_refresh_keeps_explicit_resume_semantics(tmp_path, monkeypatch, implicit):
    async with isolated_owner(tmp_path, monkeypatch) as (store, authority, owner):
        sid = await create(store, authority, owner, tmp_path, 'cursor-refresh')
        authority.db.append_message(sid, 'user', 'synthetic accepted conversation')
        before, during = (0, 0), (0, 1)
        owner.replay_cursors[sid] = before
        entered, release = asyncio.Event(), asyncio.Event()
        observed = []
        real_attach = authority.attach

        async def pause_refresh(*args, **kwargs):
            entered.set()
            await release.wait()

        async def capture_attach(*args, **kwargs):
            observed.append(kwargs.get('replay_from'))
            return await real_attach(*args, **kwargs)

        monkeypatch.setattr('gateway.session_policy_refresh.refresh_resumed_policy', pause_refresh)
        monkeypatch.setattr(authority, 'attach', capture_attach)
        params = {'breadcrumb_candidate': True} if implicit else {}
        pending = asyncio.create_task(_call(owner, 'session.resume', session_id=sid, **params))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            owner.replay_cursors[sid] = during
            release.set()
            result = await asyncio.wait_for(pending, timeout=5)
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        assert result['session_id'] == sid
        assert observed == [during if implicit else before]
        assert owner.replay_cursors.get(sid) == (None if implicit else during)


@pytest.mark.asyncio
async def test_one_read_snapshot_does_not_mix_a_concurrent_admission(tmp_path, monkeypatch):
    async with isolated_owner(tmp_path, monkeypatch) as (store, authority, owner):
        sid = await create(store, authority, owner, tmp_path, 'snapshot')
        import hermes_state_local_lineage as lineage
        from gateway.session_local_title import eligible_breadcrumb
        real_validate = lineage.validate_local_lineage
        wrote = []
        def validate_then_publish(conn, receipt):
            tip = real_validate(conn, receipt)
            if not wrote:
                with closing(sqlite3.connect(authority.db.db_path, timeout=1)) as writer:
                    assert writer.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
                    writer.execute(
                        'INSERT INTO session_admissions(admission_id,request_id,principal_id,target_session_id,'
                        'lineage_json,payload_json,payload_digest,intent,status) VALUES(?,?,?,?,?,?,?,?,?)',
                        ('concurrent', 'concurrent', 'human', sid, json.dumps([sid]), '{}', 'd', 'queue', 'queued'))
                    writer.commit()
                wrote.append(True)
            return tip
        monkeypatch.setattr(lineage, 'validate_local_lineage', validate_then_publish)
        assert eligible_breadcrumb(authority, sid) is False
        assert wrote == [True]
        assert eligible_breadcrumb(authority, sid) is True


@pytest.mark.asyncio
async def test_real_compression_continuation_preserves_hint(tmp_path, monkeypatch):
    async with isolated_owner(tmp_path, monkeypatch) as (store, authority, owner):
        sid = await create(store, authority, owner, tmp_path, 'compression')
        authority.db.append_message(sid, 'user', 'synthetic prior conversation')
        from hermes_state_mutation_compress import compress_in_transaction
        from hermes_state_mutation_prepared import local_snapshot
        def compress(conn):
            prepared = {'snapshot': local_snapshot(authority.db, conn, sid), 'in_place': False,
                        'messages': [{'role': 'user', 'content': 'synthetic compressed conversation'}]}
            return compress_in_transaction(authority.db, conn, sid, {}, prepared)
        child = authority.db._execute_write(compress)[1]['target_session_id']
        assert child != sid
        from gateway.session_local_title import eligible_breadcrumb
        assert eligible_breadcrumb(authority, sid) is True
