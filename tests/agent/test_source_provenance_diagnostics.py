"""Private receipts explain producer loss without changing source authority or wire bytes."""
import json
from hashlib import sha256
from types import SimpleNamespace
from contextvars import copy_context

import pytest

from agent.llm_egress_firewall import EgressBlocked
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.source_provenance import SourceProvenanceRegistry
from agent.source_provenance_tools import (
    attach_trusted_source_provenance_metadata, execute_with_source_provenance,
    issue_active_read_provenance,
)
from tools.file_operations import ShellFileOperations


def test_detached_receipt_context_releases_callback_and_native_scan_buffers(tmp_path):
    from agent import source_provenance_diagnostics as diagnostics
    agent = _agent(tmp_path)
    owner = (agent.session_id, agent._current_turn_id, agent._current_api_request_id,
             agent._llm_egress_policy_digest)
    segment = object()
    with diagnostics.receipt_scope(agent, agent._source_provenance_registry, owner,
                                   refusal_origins=lambda: ("other",)):
        diagnostics.record_native_metadata_scan(owner, segment, "synthetic-sensitive-canary", "safe")
        copied = copy_context()
        observation = diagnostics._receipt.get()
        assert diagnostics.native_metadata_scan_inputs(segment) is not None
    assert not observation
    assert copied.run(diagnostics.native_metadata_scan_inputs, segment) is None
    receipt = {}
    decision = SimpleNamespace(session_id=owner[0], turn_id=owner[1], request_id=owner[2],
                               policy_digest=owner[3], allowed=False)
    copied.run(diagnostics.append_receipt_diagnostics, receipt, decision)
    assert receipt == {}


def _agent(tmp_path):
    return SimpleNamespace(provider='custom', model='test-model',
        base_url='https://llm.example.test/v1', api_mode='chat_completions',
        session_id='private-session', _current_turn_id='private-turn',
        _current_api_request_id='private-turn:api:4',
        _llm_egress_policy_digest=sha256(b'private-policy').hexdigest(),
        _llm_egress_state_dir=tmp_path / 'receipts',
        _source_provenance_registry=SourceProvenanceRegistry())


def _read(agent, tmp_path, *, mismatch=False):
    source = tmp_path / 'synthetic.txt'
    source.write_text('ordinary synthetic text\n', encoding='utf-8')
    formatter = object.__new__(ShellFileOperations)
    content = formatter._add_line_numbers(source.read_text(), 1)
    result = {'content': content, 'total_lines': 1, 'file_size': source.stat().st_size}
    def execute(args):
        issue_active_read_provenance(resolved=source, source_path=source, offset=1, limit=20,
            returned_content='different presentation' if mismatch else content,
            result_dict=result, file_ops=formatter)
        return json.dumps(result, ensure_ascii=False)
    output = execute_with_source_provenance(agent, 'read_file', 'private-call', execute, {})
    metadata = attach_trusted_source_provenance_metadata(agent, 'read_file',
        content=output, tool_call_id='private-call')
    return output, metadata


def _decision(agent, *, blocked=False):
    agent._current_api_request_id = 'private-turn:api:5'
    body = {'model': 'test-model', 'messages': [{'role': 'user',
        'content': '/Users/private-owner/private-file.txt' if blocked else 'hello'}]}
    if blocked:
        with pytest.raises(EgressBlocked):
            authorize_agent_sdk_kwargs(agent, body)
    else:
        wire, auth = authorize_agent_sdk_kwargs(agent, body)
        assert wire == body
        assert json.loads(auth.payload_bytes) == body
    path = agent._llm_egress_state_dir / 'llm-egress-receipts.jsonl'
    return json.loads(path.read_text().splitlines()[-1])


def test_blocked_worker_receipt_retains_refusal_without_private_data(tmp_path):
    agent = _agent(tmp_path)
    output, metadata = _read(agent, tmp_path, mismatch=True)
    assert metadata is None
    assert 'ordinary synthetic text' in output
    receipt = _decision(agent, blocked=True)
    del agent  # readback must survive the worker's in-memory lifetime
    events = receipt.get('source_provenance_diagnostics', {}).get('events', [])
    assert {'stage': 'producer', 'code': 'presentation_mismatch'} in events
    diagnostic = json.dumps(receipt['source_provenance_diagnostics'])
    for secret in ('private-session', 'private-turn', 'private-call', 'synthetic.txt',
                   'ordinary synthetic text', '/Users/', sha256(b'private-policy').hexdigest()):
        assert secret not in diagnostic


def test_success_records_all_stages_without_changing_wire_and_hash_chain(tmp_path):
    agent = _agent(tmp_path)
    _output, metadata = _read(agent, tmp_path)
    assert metadata is not None
    first = _decision(agent)
    second = _decision(agent)
    events = first.get('source_provenance_diagnostics', {}).get('events', [])
    for stage, code in [('activation', 'active'), ('producer', 'grant_issued'),
                        ('commit', 'metadata_committed'), ('consumer', 'bound')]:
        assert any(event.get('stage') == stage and event.get('code') == code for event in events)
    first_line = (agent._llm_egress_state_dir / 'llm-egress-receipts.jsonl').read_bytes().splitlines()[0]
    for receipt in (first, second):
        digest = receipt.pop('receipt_sha256')
        material = json.dumps(receipt, ensure_ascii=True, separators=(',', ':'), sort_keys=True)
        assert digest == sha256(receipt['receipt_prev_sha256'].encode('ascii') + material.encode()).hexdigest()
    assert second['receipt_prev_sha256'] == sha256(first_line).hexdigest()
    assert not (agent._llm_egress_state_dir / 'llm-egress-receipts.jsonl').stat().st_mode & 0o077


def test_other_agent_and_other_request_do_not_receive_prior_trace(tmp_path):
    agent = _agent(tmp_path)
    _read(agent, tmp_path)
    other = _agent(tmp_path / 'other')
    for receipt in (_decision(other),):
        assert receipt.get('source_provenance_diagnostics', {}).get('events') == [
            {'stage': 'consumer', 'code': 'no_trace'}]
    agent._current_turn_id = 'different-turn'
    receipt = _decision(agent)
    assert receipt.get('source_provenance_diagnostics', {}).get('events') == [
        {'stage': 'consumer', 'code': 'no_trace'}]


@pytest.mark.parametrize('failure', ['private exception /Users/secret token=private-token',
                                     'redaction_changed_content'])
def test_registry_refusal_records_only_allowlisted_reason_and_keeps_zero_grants(tmp_path, monkeypatch, failure):
    from agent.source_provenance import SourceProvenanceError
    agent = _agent(tmp_path)
    def refuse(**kwargs):
        raise SourceProvenanceError(failure)
    monkeypatch.setattr(agent._source_provenance_registry, 'issue_file_slice', refuse)
    _, metadata = _read(agent, tmp_path)
    assert metadata is None
    assert not agent._source_provenance_registry.grants_for_request('private-turn:api:5')
    events = _decision(agent, blocked=True)['source_provenance_diagnostics']['events']
    expected = failure if failure == 'redaction_changed_content' else 'unexpected_error'
    assert {'stage': 'producer', 'code': expected} in events
    assert 'private-token' not in json.dumps(events)
    assert '/Users/' not in json.dumps(events)


def test_metadata_binding_exception_does_not_remove_existing_grant(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    def fail(*args):
        raise RuntimeError('private binding exception /Users/private-data')
    monkeypatch.setattr(agent._source_provenance_registry, 'commit_read_result', fail)
    _, metadata = _read(agent, tmp_path)
    assert metadata is None
    assert len(agent._source_provenance_registry.grants_for_request('private-turn:api:5')) == 1
    events = _decision(agent)['source_provenance_diagnostics']['events']
    assert {'stage': 'commit', 'code': 'unexpected_error'} in events
    assert 'private binding' not in json.dumps(events)


def test_registry_replacement_is_observed_without_using_old_grants(tmp_path):
    agent = _agent(tmp_path)
    _read(agent, tmp_path)
    agent._source_provenance_registry = SourceProvenanceRegistry()
    receipt = _decision(agent)
    assert receipt['source_grant_count'] == 0
    event = receipt['source_provenance_diagnostics']['events'][-1]
    assert event == {'stage': 'consumer', 'code': 'bound', 'same_registry': False,
                     'owner_matches': True, 'request_matches': True}


@pytest.mark.parametrize('stage', ['_state', '_event', '_snapshot'])
def test_diagnostic_failure_preserves_read_metadata_wire_and_block_decisions(tmp_path, monkeypatch, stage):
    from agent import source_provenance_diagnostics as diagnostics
    def fail(*args, **kwargs):
        raise RuntimeError('private diagnostic error')
    monkeypatch.setattr(diagnostics, stage, fail)
    agent = _agent(tmp_path)
    output, metadata = _read(agent, tmp_path)
    assert metadata is not None
    assert 'ordinary synthetic text' in output
    assert len(agent._source_provenance_registry.grants_for_request('private-turn:api:5')) == 1
    assert _decision(agent)['allowed'] is True
    assert _decision(agent, blocked=True)['allowed'] is False


def test_trace_overflow_is_explicit_and_does_not_cap_execution(tmp_path):
    agent = _agent(tmp_path)
    for _ in range(40):
        output, metadata = _read(agent, tmp_path, mismatch=True)
        assert metadata is None and 'ordinary synthetic text' in output
    diagnostics = _decision(agent)['source_provenance_diagnostics']
    assert diagnostics['overflow'] is True
    assert len(diagnostics['events']) == 32


def test_profile_owner_change_does_not_receive_previous_profile_trace(tmp_path):
    agent = _agent(tmp_path)
    _read(agent, tmp_path)
    agent.session_id = 'different-private-profile-session'
    assert _decision(agent)['source_provenance_diagnostics']['events'] == [
        {'stage': 'consumer', 'code': 'no_trace'}]


def test_missing_activation_identity_and_inactive_producer_are_distinct(tmp_path):
    agent = _agent(tmp_path)
    agent.session_id = ''
    _, metadata = _read(agent, tmp_path)
    assert metadata is None
    events = _decision(agent, blocked=True)['source_provenance_diagnostics']['events']
    assert {'stage': 'activation', 'code': 'missing_identity'} in events
    assert {'stage': 'producer', 'code': 'no_context'} in events


def test_relative_local_file_read_uses_actual_dispatch_adapter_and_tool(tmp_path, monkeypatch):
    from tools import file_tools
    from tools.environments.local import LocalEnvironment
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('TERMINAL_ENV', 'local')
    monkeypatch.setenv('TERMINAL_CWD', '.')
    (tmp_path / 'ordinary.txt').write_text('ordinary synthetic text\n', encoding='utf-8')
    env = LocalEnvironment(cwd='.')
    file_ops = ShellFileOperations(env)
    monkeypatch.setattr(file_tools, '_get_file_ops', lambda task_id='default': file_ops)
    agent = _agent(tmp_path)
    result = execute_with_source_provenance(agent, 'read_file', 'private-call',
        lambda args: file_tools.read_file_tool(**args), {'path': 'ordinary.txt'})
    assert json.loads(result)['content'] == '1|ordinary synthetic text'
    metadata = attach_trusted_source_provenance_metadata(agent, 'read_file',
        content=result, tool_call_id='private-call')
    assert metadata is not None
    assert {'stage': 'producer', 'code': 'grant_issued'} in _decision(agent)[
        'source_provenance_diagnostics']['events']


def test_failed_activation_is_not_reported_as_active(tmp_path):
    agent = _agent(tmp_path)
    with pytest.raises(ValueError, match='invalid_tool_call_id'):
        execute_with_source_provenance(agent, 'read_file', 'x' * 257, lambda args: 'unused', {})
    events = _decision(agent)['source_provenance_diagnostics']['events']
    assert {'stage': 'activation', 'code': 'active'} not in events
    assert {'stage': 'activation', 'code': 'unexpected_error'} in events


def test_context_cleanup_failure_does_not_replace_tool_result(tmp_path, monkeypatch):
    from agent import source_provenance_diagnostics as diagnostics
    class BrokenReset:
        def set(self, value):
            return object()
        def reset(self, token):
            raise RuntimeError('private-reset-failure')
    monkeypatch.setattr(diagnostics, '_active', BrokenReset())
    agent = _agent(tmp_path)
    assert execute_with_source_provenance(agent, 'read_file', 'call', lambda args: 'original', {}) == 'original'
    monkeypatch.setattr(diagnostics, '_receipt', BrokenReset())
    with diagnostics.receipt_scope(agent, agent._source_provenance_registry,
            ('private-session', 'private-turn', 'private-turn:api:5', agent._llm_egress_policy_digest)):
        pass


def test_block_receipt_survives_nonzero_worker_process_exit(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    child = subprocess.run([sys.executable, '-c',
        'from pathlib import Path; import sys; '
        'from tests.agent.test_source_provenance_diagnostics import _agent, _read, _decision; '
        'root=Path(sys.argv[1]); agent=_agent(root); '
        '_read(agent, root, mismatch=True); _decision(agent, blocked=True); sys.exit(7)',
        str(tmp_path)], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=30, check=False)
    assert child.returncode == 7
    receipt = json.loads((tmp_path / 'receipts' / 'llm-egress-receipts.jsonl').read_text())
    assert receipt['allowed'] is False
    assert {'stage': 'producer', 'code': 'presentation_mismatch'} in receipt[
        'source_provenance_diagnostics']['events']


def test_parallel_agents_with_identical_request_labels_keep_their_own_trace(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    def worker(name, mismatch):
        root = tmp_path / name
        root.mkdir()
        agent = _agent(root)
        _read(agent, root, mismatch=mismatch)
        return _decision(agent)['source_provenance_diagnostics']['events']
    with ThreadPoolExecutor(max_workers=2) as pool:
        rejected = pool.submit(worker, 'one', True)
        issued = pool.submit(worker, 'two', False)
        rejected_events, issued_events = rejected.result(timeout=30), issued.result(timeout=30)
    assert {'stage': 'producer', 'code': 'grant_issued'} not in rejected_events
    assert {'stage': 'producer', 'code': 'presentation_mismatch'} not in issued_events
    assert {'stage': 'producer', 'code': 'presentation_mismatch'} in rejected_events
    assert {'stage': 'producer', 'code': 'grant_issued'} in issued_events


def test_diagnostic_contexts_cannot_be_reused_after_execution_or_receipt(tmp_path):
    from agent import source_provenance_diagnostics as diagnostics
    agent = _agent(tmp_path)
    _read(agent, tmp_path, mismatch=True)
    diagnostics.producer('grant_issued')
    receipt = _decision(agent)
    assert {'stage': 'producer', 'code': 'grant_issued'} not in receipt[
        'source_provenance_diagnostics']['events']
    outside = {}
    diagnostics.append_receipt_diagnostics(outside, SimpleNamespace(
        session_id=agent.session_id, turn_id=agent._current_turn_id,
        request_id=agent._current_api_request_id, policy_digest=agent._llm_egress_policy_digest))
    assert outside == {}
