"""Canonical compression must not display an original beside its rewritten tail carrier."""
from collections import Counter
import json
import re
from types import SimpleNamespace

import pytest

from agent.context_compressor import ContextCompressor
from gateway import run
from gateway.config import GatewayConfig
from gateway.session import SessionStore
from gateway.session_authority import SessionAuthority
from gateway.session_controls import AuthorityConnection
from gateway.session_local import create_local_session
from hermes_state import SessionDB
import hermes_state_runtime as rt


@pytest.mark.asyncio
@pytest.mark.parametrize('focus', ['', 'here 1', 'here 2'])
@pytest.mark.parametrize('system_in_tail', [False, True])
@pytest.mark.parametrize('legacy_uids', [False, True, 'backfilled'])
@pytest.mark.parametrize('prior_generation', [False, True])
async def test_compress_and_resume_keep_each_original_reply_once(
        tmp_path, monkeypatch, focus, system_in_tail, legacy_uids, prior_generation):
    monkeypatch.setattr(run, '_load_gateway_config', lambda *args: {
        'compression': {'protect_first_n': 3, 'protect_last_n': 2, 'min_tail_user_messages': 1},
        'platform_toolsets': {'cli': []},
    })
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store._db
    backfilled = {}

    def summarize(self, prompt, started_at):
        if legacy_uids == 'backfilled':
            # Another opener can advance bounded migration while the summary is prepared.
            db._write_sql('UPDATE messages SET message_uid=lower(hex(randomblob(16))) WHERE message_uid IS NULL')
            backfilled.update((row['id'], row['message_uid']) for row in db._read_all(
                'SELECT id, message_uid FROM messages'))
        return 'SUMMARYMARK compressed account of the earlier turns'

    monkeypatch.setattr(ContextCompressor, '_call_summary_llm', summarize)
    runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                             _cached_agent_for=lambda route: None, _evict_cached_agent=lambda *a, **kw: None,
                             _resolve_session_agent_runtime=lambda **kwargs: ('fake-model', {
                                 'provider': 'custom', 'base_url': 'http://127.0.0.1:9/v1',
                                 'api_key': 'fixture-only'}))
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = SessionAuthority(runner, profile_id=str(tmp_path), instance_id='owner', db=db,
                                 epoch=rt.begin_runtime_epoch(db, instance_id='owner'))
    owner = AuthorityConnection(authority, object(), {'user_id': 'human'})
    try:
        ref = create_local_session(authority, owner.actor, dict(
            request_id='transcript', source='cli', cwd=str(tmp_path), model='fake-model', toolsets=[]))
        system_row = None
        for turn in range(1, 8):
            db.append_message(ref.session_id, 'user', f'long session turn zq{turn}q')
            db.append_message(ref.session_id, 'assistant', ' '.join(f's{turn}w{i:03d}' for i in range(200)))
            if system_in_tail and turn == 6:
                system_row = db.append_message(ref.session_id, 'system', 'SYSTEMMARK retained session note')
        if prior_generation:
            db.archive_and_compact(ref.session_id, db.get_messages_as_conversation(ref.session_id))
        if legacy_uids:
            # Bounded migration can leave legacy rows without logical identities.
            db._write_sql('UPDATE messages SET message_uid=NULL WHERE session_id=?', (ref.session_id,))
        before = db.get_session(ref.session_id)
        response = await owner.dispatch({'id': 1, 'method': 'session.mutate', 'params': {
            'session_id': ref.session_id, 'request_id': 'compress', 'operation': 'compress', 'payload': {'focus': focus},
            'expected_revision': before['runtime_revision'], 'expected_generation': before['runtime_generation']}})
        assert 'result' in response, response
        assert response['result']['message_count'] < 14
        for row_id, uid in backfilled.items():
            assert db._read_one('SELECT message_uid FROM messages WHERE id=?', (row_id,))[0] == uid
        expected = Counter({f's{turn}w{i:03d}': 1 for turn in range(1, 8) for i in range(200)})
        for _ in range(2):
            resumed = await owner.dispatch({'id': 2, 'method': 'session.resume',
                                            'params': {'session_id': ref.session_id}})
            assert 'result' in resumed, resumed
            text = '\n'.join(str(m.get('content', '')) for m in resumed['result']['messages'])
            assert Counter(re.findall(r'\bs\dw\d{3}\b', text)) == expected
            assert text.count('SYSTEMMARK') == int(system_in_tail)
        if system_in_tail:
            # This row never reached the compressor, so it cannot be a superseded tail copy.
            assert system_row is not None
            assert tuple(db._read_one('SELECT active, compacted FROM messages WHERE id=?', (system_row,))) == (0, 1)
            for displayed in (
                db.get_messages_as_conversation(ref.session_id, include_compacted=True),
                db.get_messages(ref.session_id, include_compacted=True),
            ):
                text = '\n'.join(str(m.get('content', '')) for m in displayed)
                assert text.index('s6w199') < text.index('SYSTEMMARK') < text.index('s7w000')
        for generation in range(2):
            # Check the donor before portability: export/import must not be blamed for a
            # duplicate already introduced by the unchanged archive/reinsert boundary.
            for reader in ('get_messages_as_conversation', 'get_messages'):
                displayed = getattr(db, reader)(ref.session_id, include_compacted=True)
                text = '\n'.join(str(m.get('content', '')) for m in displayed)
                assert Counter(re.findall(r'\bs\dw\d{3}\b', text)) == expected, (generation, reader)
            backup = json.loads(json.dumps(db.export_session(ref.session_id, include_inactive=True)))
            restored = SessionDB(db_path=tmp_path / f'restored-{generation}.db')
            try:
                assert restored.import_sessions([backup])['ok']
                for reader in ('get_messages_as_conversation', 'get_messages'):
                    shape = lambda database: [(m['role'], m.get('content')) for m in getattr(database, reader)(
                        ref.session_id, include_compacted=True)]
                    assert shape(restored) == shape(db)
                text = '\n'.join(str(m.get('content', '')) for m in restored.get_messages(
                    ref.session_id, include_compacted=True))
                assert Counter(re.findall(r'\bs\dw\d{3}\b', text)) == expected
                if system_in_tail:
                    assert text.index('s6w199') < text.index('SYSTEMMARK') < text.index('s7w000')
            finally:
                restored.close()
            if generation == 0:
                # An unchanged later generation must retain an earlier carrier's chronology.
                live_before = db.get_messages(ref.session_id)
                summary_uids = {m['message_uid'] for m in live_before if m.get('_compressed_summary')}
                replay = db.get_messages_as_conversation(ref.session_id)
                db.archive_and_compact(ref.session_id, replay)
                live_after = db.get_messages(ref.session_id)
                assert [(m['role'], m.get('content')) for m in live_after] == [
                    (m['role'], m.get('content')) for m in live_before]
                assert {m['message_uid'] for m in live_after if m.get('_compressed_summary')} == summary_uids
    finally:
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_canonical_compress_retains_full_tool_result(tmp_path, monkeypatch):
    """A pruned replay stub must not hide the archived full tool output."""
    monkeypatch.setattr(run, '_load_gateway_config', lambda *args: {
        'compression': {'protect_first_n': 3, 'protect_last_n': 2, 'min_tail_user_messages': 1},
        'platform_toolsets': {'cli': []}})
    monkeypatch.setattr(ContextCompressor, '_call_summary_llm', lambda *args: 'SUMMARYMARK earlier turns')
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store._db
    runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
        _cached_agent_for=lambda route: None, _evict_cached_agent=lambda *a, **kw: None,
        _resolve_session_agent_runtime=lambda **kw: ('fake-model', {
            'provider': 'custom', 'base_url': 'http://127.0.0.1:9/v1', 'api_key': 'fixture-only'}))
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = SessionAuthority(runner, profile_id=str(tmp_path), instance_id='owner', db=db,
        epoch=rt.begin_runtime_epoch(db, instance_id='owner'))
    owner = AuthorityConnection(authority, object(), {'user_id': 'human'})
    try:
        ref = create_local_session(authority, owner.actor, dict(request_id='transcript', source='cli',
            cwd=str(tmp_path), model='fake-model', toolsets=[]))
        for turn in range(1, 7):
            db.append_message(ref.session_id, 'user', f'turn {turn}')
            db.append_message(ref.session_id, 'assistant', 'historical detail ' * 200)
        payload = 'UNIQUE_FULL_TOOL_PAYLOAD ' + ('R' * 200_000)
        db.append_messages_batch(ref.session_id, [
            {'role': 'user', 'content': 'read the file'},
            {'role': 'assistant', 'content': 'reading', 'tool_calls': [{'id': 'read-tail', 'type': 'function',
                'function': {'name': 'read_file', 'arguments': json.dumps({'path': 'example.txt'})}}]},
            {'role': 'tool', 'tool_call_id': 'read-tail', 'tool_name': 'read_file', 'content': payload},
            *[message for i in range(3) for message in (
                {'role': 'assistant', 'content': 'more', 'tool_calls': [{'id': f'next-{i}', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}]},
                {'role': 'tool', 'content': f'small output {i}', 'tool_call_id': f'next-{i}', 'tool_name': 'read_file'})],
            {'role': 'assistant', 'content': 'Finished reading'}])
        before = db.get_session(ref.session_id)
        response = await owner.dispatch({'id': 1, 'method': 'session.mutate', 'params': {
            'session_id': ref.session_id, 'request_id': 'compress', 'operation': 'compress', 'payload': {},
            'expected_revision': before['runtime_revision'], 'expected_generation': before['runtime_generation']}})
        assert 'result' in response, response
        rows = db.get_messages(ref.session_id, include_inactive=True)
        full = [r for r in rows if r['role'] == 'tool' and r['content'] == payload]
        assert len(full) == 1 and (full[0]['active'], full[0]['compacted']) == (0, 1)
        live = db.get_messages_as_conversation(ref.session_id)
        assert payload not in [m.get('content') for m in live]
        assert any(m['role'] == 'tool' and m.get('tool_call_id') == 'read-tail'
                   and len(m.get('content', '')) < len(payload) for m in live)
        for reader in ('get_messages', 'get_messages_as_conversation'):
            shown = getattr(db, reader)(ref.session_id, include_compacted=True)
            assert payload in [m.get('content') for m in shown], reader
    finally:
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.parametrize('copy_kind', [
    'unchanged', 'changed', 'foreign', 'changed_tools', 'changed_api', 'explicit_false', 'explicit_zero'])
def test_unchanged_compaction_copy_keeps_only_its_own_summary_classification(tmp_path, copy_kind):
    with SessionDB(db_path=tmp_path / 'carrier.db') as db:
        db.create_session('source', source='cli')
        db.create_session('other', source='cli')
        db.append_messages_batch('source', [
            {'role': 'assistant', 'content': 'original reply and summary', 'timestamp': 2.0,
             '_compressed_summary': True}])
        projected = db.get_messages_as_conversation('source')
        assert not projected[0].get('_compressed_summary')  # the ordinary replay projection
        mutations = {
            'unchanged': {},
            'foreign': {},
            'changed': {'content': 'replacement reply'},
            'changed_tools': {'tool_calls': [{'id': 'new-call', 'type': 'function',
                'function': {'name': 'read_file', 'arguments': '{}'}}]},
            'changed_api': {'api_content': 'different provider payload'},
            'explicit_false': {'_compressed_summary': False},
            'explicit_zero': {'_compressed_summary': 0},
        }
        projected[0].update(mutations[copy_kind])
        target = 'other' if copy_kind == 'foreign' else 'source'
        db.archive_and_compact(target, projected)
        stored = db.get_messages(target)
        assert len(stored) == 1
        assert bool(stored[0].get('_compressed_summary')) == (copy_kind == 'unchanged')
        assert stored[0]['content'] == projected[0]['content']


@pytest.mark.parametrize('marker', ['omitted', False, 0, True], ids=['omitted', 'false', 'zero', 'true'])
def test_failed_compaction_restores_summary_annotation_before_retry(tmp_path, monkeypatch, marker):
    with SessionDB(db_path=tmp_path / 'carrier-rollback.db') as db:
        db.create_session('source', source='cli')
        db.append_messages_batch('source', [
            {'role': 'assistant', 'content': 'original reply and summary', 'timestamp': 2.0,
             '_compressed_summary': True}])
        projected = db.get_messages_as_conversation('source')
        if marker != 'omitted':
            projected[0]['_compressed_summary'] = marker
        before_input = dict(projected[0])
        before_rows = db.get_messages('source', include_inactive=True)
        before_session = db.get_session('source')
        insert = db._insert_message_rows

        def fail_after_insert(conn, session_id, messages, **kwargs):
            insert(conn, session_id, messages, **kwargs)
            assert messages[0]['_compressed_summary'] is (True if marker == 'omitted' else marker)
            raise RuntimeError('compaction publication failed')

        with monkeypatch.context() as attempt:
            attempt.setattr(db, '_insert_message_rows', fail_after_insert)
            with pytest.raises(RuntimeError, match='compaction publication failed'):
                db.archive_and_compact('source', projected)
        assert db.get_messages('source', include_inactive=True) == before_rows
        assert db.get_session('source') == before_session
        assert projected[0] == before_input
        if marker != 'omitted':
            assert projected[0]['_compressed_summary'] is marker
        else:
            assert '_compressed_summary' not in projected[0]

        # A subsequent attempt no longer has a marked donor. It must not reuse a
        # classification inferred in the rolled-back attempt; explicit values survive.
        db._write_sql('UPDATE messages SET _compressed_summary=0 WHERE session_id=?', ('source',))
        db.archive_and_compact('source', projected)
        stored = db.get_messages('source')
        assert len(stored) == 1
        assert bool(stored[0].get('_compressed_summary')) is (marker is True)
        assert stored[0]['content'] == 'original reply and summary'
