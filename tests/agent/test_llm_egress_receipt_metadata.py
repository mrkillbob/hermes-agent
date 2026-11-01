"""Real allow/block receipts gain metadata without changing authority or wire bytes."""
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest
from agent import llm_egress_firewall as firewall
from agent.llm_egress_runtime import authorize_agent_sdk_kwargs
from agent.llm_egress_receipt_metadata import RECEIPT_SCHEMA, RENDERER_SCHEMA
from agent.source_provenance import SourceProvenanceRegistry


@pytest.mark.parametrize('blocked', [False, True])
def test_metadata_preserves_wire_privacy_and_legacy_hash_chain(tmp_path, monkeypatch, blocked):
    agent = SimpleNamespace(provider='custom', model='test-model',
        base_url='https://llm.example.test/v1', api_mode='chat_completions',
        session_id='private-session', _current_turn_id='private-turn',
        _current_api_request_id='private-turn:api:1',
        _llm_egress_policy_digest=sha256(b'private-policy').hexdigest(),
        _llm_egress_state_dir=tmp_path / 'receipts',
        _source_provenance_registry=SourceProvenanceRegistry())
    ordinary = {'model': 'test-model', 'messages': [{'role': 'user', 'content': 'hello'}]}
    # An old-format writer used exactly asdict; retain its real receipt unchanged.
    with monkeypatch.context() as legacy:
        legacy.setattr(firewall, 'receipt_envelope', asdict)
        wire, authorization = authorize_agent_sdk_kwargs(agent, deepcopy(ordinary))
        assert wire == ordinary and json.loads(authorization.payload_bytes) == ordinary
    path = tmp_path / 'receipts' / 'llm-egress-receipts.jsonl'
    old_bytes = path.read_bytes()
    agent._current_api_request_id = 'private-turn:api:2'
    body = deepcopy(ordinary)
    if blocked:
        body['messages'][0]['content'] = '/Users/private-owner/private-file.txt'
    before = deepcopy(body)
    earliest = datetime.now(UTC)
    if blocked:
        with pytest.raises(firewall.EgressBlocked) as error:
            authorize_agent_sdk_kwargs(agent, body)
        assert 'private_absolute_path' in error.value.decision.reason_codes
    else:
        wire, authorization = authorize_agent_sdk_kwargs(agent, body)
        assert wire == before and json.loads(authorization.payload_bytes) == before
    latest = datetime.now(UTC)
    assert body == before
    data = path.read_bytes()
    assert data.startswith(old_bytes)
    previous, current = data.splitlines()
    receipt = json.loads(current)
    recorded = datetime.fromisoformat(receipt['recorded_at'])
    assert recorded.utcoffset() == timedelta(0) and earliest <= recorded <= latest
    assert receipt['recorded_at'].endswith('+00:00')
    assert receipt['receipt_schema'] == RECEIPT_SCHEMA
    assert receipt['renderer_schema'] == RENDERER_SCHEMA
    assert receipt['allowed'] is not blocked
    assert receipt['receipt_prev_sha256'] == sha256(previous).hexdigest()
    digest = receipt.pop('receipt_sha256')
    material = json.dumps(receipt, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode()
    assert digest == sha256(receipt['receipt_prev_sha256'].encode('ascii') + material).hexdigest()
    assert receipt['session_id'] == agent.session_id  # Existing safe correlation IDs remain intact.
    for private in ('private-owner', 'private-file.txt', '/Users/'):
        assert private not in current.decode()
