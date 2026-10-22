"""Target-advancing mutations (reset, compress/model prepare) fence executing work only.

A queued follower is what /reset and compression exist to serve: it waits on the logical
owner and runs against whatever physical target the mutation publishes.
"""
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
import sqlite3

import pytest

from hermes_state import SessionDB
import hermes_state_runtime as rt


def _local_session(db, epoch, cwd):
    from hermes_state_local import commit_local_session
    from gateway.config import Platform
    from gateway.session import SessionEntry, SessionSource
    from gateway.session_lifecycle import _now
    from gateway.session_local_recovery import local_identity
    from gateway.session_policy import build_policy
    sid = local_identity('profile', 'human', 'r')
    source = SessionSource(platform=Platform.LOCAL, chat_id=sid, user_id='human', chat_type='dm')
    now = _now()
    entry = SessionEntry('local:' + sid, sid, now, now, origin=source, platform=Platform.LOCAL)
    policy = build_policy({'source': 'cli', 'cwd': str(cwd), 'model': 'm', 'toolsets': []},
                          {'platform_toolsets': {'cli': []}}, private_secrets={})
    commit_local_session(db, epoch=epoch, receipt={
        'profile_id': 'profile', 'principal_id': 'human', 'request_id': 'r', 'session_id': sid,
        'route': entry.session_key, 'entry': entry.to_dict(), 'policy': asdict(policy)})
    return sid


def _mutate(db, epoch, sid, operation, **extra):
    session = db.get_session(sid)
    return rt.mutate_runtime_session(db, epoch=epoch, principal_id='human', session_id=sid,
        request_id=operation + '-' + str(session['runtime_revision']), operation=operation,
        expected_revision=session['runtime_revision'], expected_generation=session['runtime_generation'],
        payload={}, **extra)


@pytest.mark.parametrize('operation', ['reset', 'compress'])
def test_target_advance_allows_queued_follower_and_refuses_started_head(tmp_path, operation):
    prepare = {'_prepare_only': True} if operation == 'compress' else {}
    with closing(SessionDB(tmp_path / 'state.db')) as db:
        epoch = rt.begin_runtime_epoch(db, instance_id='owner')
        sid = _local_session(db, epoch, tmp_path)
        rt.admit_session_input(db, epoch=epoch, principal_id='human', session_id=sid,
                               request_id='head', payload={'text': 'head'})
        rt.admit_session_input(db, epoch=epoch, principal_id='human', session_id=sid,
                               request_id='follower', payload={'text': 'follower'})
        # Head and follower both queued (a paused FIFO): the mutation proceeds.
        receipt = _mutate(db, epoch, sid, operation, **prepare)
        assert ('snapshot' in receipt) if operation == 'compress' else receipt['target_session_id'] != sid
        statuses = [r['status'] for r in rt.list_session_admissions(db, session_id=sid)]
        assert statuses == ['queued', 'queued']
        # Once the head is claimed the same mutation is refused and nothing moves.
        head = rt.claim_session_input(db, epoch=epoch, session_id=sid)
        before = db.get_session(sid)
        with pytest.raises(rt.RuntimeStoreError, match='session_busy'):
            _mutate(db, epoch, sid, operation, **prepare)
        after = db.get_session(sid)
        assert (after['runtime_generation'], after['runtime_revision']) == (
            before['runtime_generation'], before['runtime_revision'])
        # The head still settles against its own generation, and the follower stays queued.
        rt.settle_session_input(db, epoch=epoch, admission_id=head['admission_id'],
                                generation=head['generation'], outcome='completed')
        assert [r['request_id'] for r in rt.list_session_admissions(db, session_id=sid)] == ['follower']


@pytest.mark.parametrize('automatic_retry', [False, True])
def test_compress_receipt_retries_do_not_reuse_rolled_back_message_annotations(
        tmp_path, monkeypatch, automatic_retry):
    with closing(SessionDB(tmp_path / 'receipt-retry.db')) as db:
        epoch = rt.begin_runtime_epoch(db, instance_id='owner')
        sid = _local_session(db, epoch, tmp_path)
        db.append_messages_batch(sid, [
            {'role': 'assistant', 'content': 'original reply and summary', 'timestamp': 2.0,
             '_compressed_summary': True}])
        prepared = dict(_mutate(db, epoch, sid, 'compress', _prepare_only=True),
                        messages=db.get_messages_as_conversation(sid), carried_messages=[], in_place=True)
        original_prepared = deepcopy(prepared)
        before_rows = db.get_messages(sid, include_inactive=True)
        before_session = db.get_session(sid)
        args = dict(epoch=epoch, principal_id='human', session_id=sid, request_id='compress-retry',
                    operation='compress', payload={}, expected_revision=before_session['runtime_revision'],
                    expected_generation=before_session['runtime_generation'], _prepared=prepared)
        insert = db._insert_message_rows
        attempts = []

        def fail_first_insert(conn, target, messages, **kwargs):
            inserted = insert(conn, target, messages, **kwargs)
            attempts.append(bool(messages[0].get('_compressed_summary')))
            if len(attempts) == 1:
                assert attempts == [True], 'the first attempt must infer its live donor classification'
                error = sqlite3.OperationalError('database is locked') if automatic_retry else RuntimeError('publish failed')
                raise error
            return inserted

        def change_donor_after_rollback(*_args):
            assert db.get_messages(sid, include_inactive=True) == before_rows
            assert db.get_session(sid) == before_session
            assert prepared == original_prepared
            assert not db.list_meta_prefix('gateway.mutation.v1.')
            # Classification changed while no transaction holds the write lock. Same UID/body
            # is no longer a marked donor; the next attempt must prove its marker again.
            db._write_sql('UPDATE messages SET _compressed_summary=0 WHERE session_id=?', (sid,))
            return True

        monkeypatch.setattr(db, '_insert_message_rows', fail_first_insert)
        monkeypatch.setattr(db, '_sleep_before_write_retry', change_donor_after_rollback)
        if automatic_retry:
            result = rt.mutate_runtime_session(db, **args)
        else:
            with pytest.raises(RuntimeError, match='publish failed'):
                rt.mutate_runtime_session(db, **args)
            change_donor_after_rollback()
            result = rt.mutate_runtime_session(db, **args)
        assert attempts == [True, False]
        assert prepared == original_prepared
        assert result['revision'] == before_session['runtime_revision'] + 1
        assert result['execution_generation'] == before_session['runtime_generation'] + 1
        stored = db.get_messages(sid)
        assert len(stored) == 1 and not stored[0].get('_compressed_summary')
        assert stored[0]['content'] == 'original reply and summary'
        assert rt.mutate_runtime_session(db, **args) == result
        assert db.get_messages(sid) == stored


@pytest.mark.parametrize(('in_place', 'carried_form'), [
    (False, 'absent'), (False, 'aliased'), (True, 'aliased')])
def test_prepared_compression_keeps_optional_carried_shape_and_caller_messages(
        tmp_path, in_place, carried_form):
    with closing(SessionDB(tmp_path / 'prepared-shape.db')) as db:
        epoch = rt.begin_runtime_epoch(db, instance_id='owner')
        sid = _local_session(db, epoch, tmp_path)
        db.append_message(sid, 'user', 'durable question')
        prepared = dict(_mutate(db, epoch, sid, 'compress', _prepare_only=True),
                        messages=db.get_messages_as_conversation(sid, include_row_ids=True), in_place=in_place)
        if carried_form == 'aliased':
            prepared['carried_messages'] = prepared['messages']
        before = deepcopy(prepared)
        result = _mutate(db, epoch, sid, 'compress', _prepared=prepared)
        target = result['target_session_id']
        assert (target == sid) is in_place
        assert [m['content'] for m in db.get_messages(target)] == ['durable question']
        assert prepared == before
        assert ('carried_messages' in prepared) is (carried_form == 'aliased')
        if carried_form == 'aliased':
            assert prepared['carried_messages'] is prepared['messages']
