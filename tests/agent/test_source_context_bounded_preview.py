"""Oversized discovered guidance retains the existing renderer under a bounded proof."""
import pytest

from tests.agent.test_source_read_result_binding import authorize, produce


@pytest.mark.parametrize('shape', ['bytes', 'lines', 'single_line'])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_oversized_guidance_keeps_head_tail_and_exact_wire_history(tmp_path, monkeypatch, shape, newline):
    middle = {'bytes':('source guidance. '*30+newline)*600,
              'lines':('source guidance.'+newline)*2001,
              'single_line':'source guidance. '*16000}[shape]
    text = 'head context'+newline+middle+'tail context'
    agent, messages, _ = produce(tmp_path, monkeypatch, count=1, guidance_override=text)
    wire = messages[0]['content']
    assert 'head context' in wire and 'tail context' in wire
    assert 'truncated AGENTS.md' in wire
    assert '[UNAVAILABLE:' not in wire
    for number in (2, 3, 4):
        authorized, receipt = authorize(agent, messages, number)
        assert authorized['messages'][0]['content'] == wire
        assert receipt.decision.source_grant_count == 2


@pytest.mark.parametrize('text', ['head context\n'+'x\n'*2001+'tail context',
                                  '\ufeffhead context\r\n'+'é guidance\r\n'*24000+'tail context'])
def test_many_short_lines_and_multibyte_bom_preserve_normalized_display(tmp_path, monkeypatch, text):
    from agent.subdirectory_hints import SubdirectoryHintTracker
    agent, messages, paths = produce(tmp_path, monkeypatch, count=1, guidance_override=text)
    original = SubdirectoryHintTracker(str(paths[0].parent.parent)).check_tool_call('read_file', {'path':str(paths[0])})
    assert messages[0]['content'].endswith(original)
    assert 'head context' in original and 'tail context' in original
    assert '\r' not in original and '\ufeff' not in original
    authorized, receipt = authorize(agent, messages)
    assert authorized['messages'][0]['content'] == messages[0]['content']
    assert receipt.decision.source_grant_count == 2


def proof(tmp_path, *, raw=None, alias=False):
    from agent.source_provenance import SourceProvenanceRegistry
    root = tmp_path.resolve()
    area = root/'gateway'
    area.mkdir(parents=True)
    target = area/'AGENTS.md'
    if alias:
        target = root/'approved.md'
        (area/'AGENTS.md').symlink_to(target)
    data = raw if raw is not None else ('head context\n'+'! '*140000+'\ntail context').encode()
    target.write_bytes(data)
    registry = SourceProvenanceRegistry()
    grant = registry.issue_context_preview(path=target, raw_bytes=data, working_root=root,
        relative_path='gateway/AGENTS.md', identity=dict(session_id='session-1', turn_id='turn-1',
                                                      request_id='turn-1:api:2', policy_digest='policy-1'))
    return registry, grant, target


def preview_request(grant, text, kind=None):
    from agent.llm_egress_firewall import OutboundText, SourcePresentationSegment, source_grant_digest
    from agent.source_context_preview import PREVIEW_PROOF_KIND
    from tests.agent.test_llm_egress_firewall import _source_presentation_request
    request = _source_presentation_request(grant, text)
    request.payload['messages'][0]['content'] = OutboundText((SourcePresentationSegment(
        source_grant_digest(grant), text, kind or PREVIEW_PROOF_KIND+grant.relative_alias),))
    return request


@pytest.mark.parametrize('case', ['secret', 'private_path', 'encoded'])
def test_hidden_middle_sensitive_content_is_not_approved_by_visible_preview(tmp_path, case):
    from agent.source_provenance import SourceProvenanceError
    hidden = {'secret':' api_key = sk-123456789012345678901234567890 ',
              'private_path':' /Users/private-person/projects/private-project ',
              'encoded':' c2VjcmV0LXBheWxvYWQ= '}[case]
    raw = ('head context\n'+'! '*70000+hidden+'! '*70000+'\ntail context').encode()
    with pytest.raises(SourceProvenanceError):
        proof(tmp_path, raw=raw)


@pytest.mark.parametrize('kind', ['bound', 'read_file_json_v1', 'context_file_normalized_v1', 'context_hint_preview_v1:gateway/AGENTS.md'])
def test_preview_proof_never_grants_ordinary_source_or_read_presentation_rights(tmp_path, kind):
    from agent.llm_egress_firewall import EgressBlocked, OutboundText, SourceBoundSegment, source_grant_digest
    from agent.source_context_preview import verify_preview
    from tests.agent.test_llm_egress_firewall import firewall, _route
    _, grant, _ = proof(tmp_path)
    text = verify_preview(grant)[0].decode()
    request = preview_request(grant, text, kind)
    if kind == 'bound':
        request.payload['messages'][0]['content'] = OutboundText((SourceBoundSegment(source_grant_digest(grant)),))
    with pytest.raises(EgressBlocked):
        firewall(tmp_path/'egress', policy_digest='policy-1').authorize(request, _route(), grants=(grant,))


@pytest.mark.parametrize('case', ['middle_changed', 'alias_retarget', 'forged_display', 'wrong_alias', 'owner', 'policy', 'request'])
def test_snapshot_alias_display_and_identity_changes_are_refused(tmp_path, case):
    from dataclasses import replace
    from agent.llm_egress_firewall import EgressBlocked
    from agent.source_context_preview import verify_preview
    from tests.agent.test_llm_egress_firewall import firewall, _route
    _, grant, target = proof(tmp_path, alias=True)
    text = verify_preview(grant)[0].decode()
    request = preview_request(grant, text)
    def change_middle():
        raw = target.read_bytes(); target.write_bytes(raw[:140000]+b'changed middle'+raw[140014:])
        return request
    def retarget():
        other = target.with_name('replacement.md'); other.write_bytes(target.read_bytes())
        alias = tmp_path/'gateway/AGENTS.md'; alias.unlink(); alias.symlink_to(other)
        return request
    request = {
        'middle_changed':change_middle, 'alias_retarget':retarget,
        'forged_display':lambda: preview_request(grant, text.replace('head context', 'fake context')),
        'wrong_alias':lambda: preview_request(grant, text, 'context_hint_bounded_preview_v1:gateway/CLAUDE.md'),
        'owner':lambda: replace(request, session_id='other-owner'),
        'policy':lambda: replace(request, policy_digest='other-policy'),
        'request':lambda: replace(request, request_id='turn-1:api:3'),
    }[case]()
    with pytest.raises(EgressBlocked):
        firewall(tmp_path/'egress', policy_digest=request.policy_digest).authorize(request, _route(), grants=(grant,))


def test_hidden_middle_configured_exact_secret_is_scanned_at_final_consumption(tmp_path):
    from agent.llm_egress_firewall import EgressBlocked
    from agent.source_context_preview import verify_preview
    from tests.agent.test_llm_egress_firewall import firewall, _route
    hidden = 'private phrase across omitted middle'
    raw = ('head context\n'+'! '*70000+hidden+'! '*70000+'\ntail context').encode()
    _, grant, _ = proof(tmp_path, raw=raw)
    text = verify_preview(grant)[0].decode()
    assert hidden not in text
    with pytest.raises(EgressBlocked) as caught:
        firewall(tmp_path/'egress', policy_digest='policy-1', exact_secret_values=(hidden,)).authorize(
            preview_request(grant, text), _route(), grants=(grant,))
    assert 'exact_secret_detected' in caught.value.decision.reason_codes


def test_preview_registry_budget_and_admission_cleanup_remain_real(tmp_path):
    from agent.source_provenance import SourceProvenanceError
    registry, grant, target = proof(tmp_path)
    raw = target.read_bytes()
    kwargs = dict(path=target, raw_bytes=raw, working_root=grant.working_root, relative_path=grant.relative_alias,
                  identity=dict(session_id=grant.session_id, turn_id=grant.turn_id,
                                request_id=grant.request_id, policy_digest=grant.policy_digest))
    for _ in range(63):
        registry.issue_context_preview(**kwargs)
    assert len(registry.grants_for_request(grant.request_id)) == 64
    with pytest.raises(SourceProvenanceError, match='grant_limit_exceeded'):
        registry.issue_context_preview(**kwargs)
    registry.clear_request(grant.request_id)
    assert not registry.grants_for_request(grant.request_id)
    assert registry.rebind_validated_presentation('unknown', original_request_id=grant.request_id,
        session_id=grant.session_id, turn_id=grant.turn_id, request_id='turn-1:api:3', policy_digest=grant.policy_digest) is None


def test_preview_replay_requires_final_admission_and_unchanged_whole_snapshot(tmp_path):
    from agent.llm_egress_firewall import source_grant_digest
    registry, grant, target = proof(tmp_path)
    kwargs = dict(original_request_id=grant.request_id, session_id=grant.session_id, turn_id=grant.turn_id,
                  request_id='turn-1:api:3', policy_digest=grant.policy_digest)
    digest = source_grant_digest(grant)
    assert registry.rebind_validated_presentation(digest, **kwargs) is None
    # The real runtime calls this boundary only after full final admission.
    from agent.source_context_preview import verify_preview
    from tests.agent.test_llm_egress_firewall import firewall, _route
    text = verify_preview(grant)[0].decode()
    firewall(tmp_path/'egress', policy_digest='policy-1').authorize(preview_request(grant, text), _route(), grants=(grant,))
    registry.remember_validated_presentations((grant,))
    assert registry.rebind_validated_presentation(digest, **kwargs) is not None
    raw = target.read_bytes(); target.write_bytes(raw[:140000]+b'changed middle'+raw[140014:])
    assert registry.rebind_validated_presentation(digest, **kwargs) is None
    registry.clear_turn(grant.turn_id)
    assert registry.rebind_validated_presentation(digest, **kwargs) is None


def test_original_long_omitted_heading_display_is_not_subject_to_a_new_character_limit(tmp_path, monkeypatch):
    from agent.subdirectory_hints import SubdirectoryHintTracker
    headings = ''.join('# '+('heading context '*120)+'\n'+('! '*300)+'\n' for _ in range(15))
    text = 'head context\n'+'! '*70000+headings+'! '*70000+'\ntail context'
    agent, messages, paths = produce(tmp_path, monkeypatch, count=1, guidance_override=text)
    original = SubdirectoryHintTracker(str(paths[0].parent.parent)).check_tool_call('read_file', {'path':str(paths[0])})
    assert messages[0]['content'].endswith(original)
    assert len(original) > 32000
    authorized, _ = authorize(agent, messages)
    assert authorized['messages'][0]['content'] == messages[0]['content']


def test_same_bytes_replacement_cannot_change_the_snapshot_inode(tmp_path):
    from agent.source_provenance import SourceProvenanceError
    from agent.source_context_preview import verify_preview
    _, grant, target = proof(tmp_path)
    replacement = target.with_name('replacement.md')
    replacement.write_bytes(target.read_bytes())
    replacement.replace(target)
    with pytest.raises(SourceProvenanceError):
        verify_preview(grant)


def test_preview_cannot_acquire_an_ordinary_read_result_prefix_binding(tmp_path):
    from hashlib import sha256
    registry, grant, _ = proof(tmp_path)
    content = '{"content":"1|value = 1"}'
    registry.remember_producer_read(grant, 'call_forged_prefix', sha256(content.encode()).hexdigest())
    owner = (grant.session_id, grant.turn_id, grant.request_id, grant.policy_digest)
    assert registry.commit_read_result(owner, 'call_forged_prefix', content) is None


def test_preview_snapshot_retains_existing_context_read_deadline(tmp_path, monkeypatch):
    from threading import Event
    from agent import source_context_preview, prompt_builder
    from agent.source_provenance import SourceProvenanceError
    started, release = Event(), Event()
    def blocked(_path):
        started.set()
        release.wait(5)
        raise OSError('generated blocked reader')
    monkeypatch.setattr(source_context_preview, '_snapshot_fd', blocked)
    monkeypatch.setattr(prompt_builder, '_get_context_file_read_timeout', lambda: 0.02)
    try:
        with pytest.raises(SourceProvenanceError):
            source_context_preview.snapshot(tmp_path/'generated.md')
        assert started.wait(2)
    finally:
        release.set()


def test_preview_target_read_policy_is_rechecked_at_consumption(tmp_path, monkeypatch):
    from agent import source_provenance
    from agent.source_context_preview import verify_preview
    from agent.llm_egress_firewall import EgressBlocked
    from tests.agent.test_llm_egress_firewall import firewall, _route
    _, grant, _ = proof(tmp_path)
    text = verify_preview(grant)[0].decode()
    monkeypatch.setattr(source_provenance, 'get_read_block_error', lambda _path: 'denied')
    with pytest.raises(EgressBlocked):
        firewall(tmp_path/'egress', policy_digest='policy-1').authorize(preview_request(grant, text), _route(), grants=(grant,))


@pytest.mark.parametrize('case', ['marker', 'encoded_path', 'source_cap'])
def test_preview_marker_path_and_proof_budget_remain_independently_checked(tmp_path, case):
    from dataclasses import replace
    from agent.llm_egress_firewall import EgressBlocked
    from agent.source_context_preview import verify_preview
    from tests.agent.test_llm_egress_firewall import firewall, _route
    _, grant, target = proof(tmp_path)
    def encoded_path():
        area = tmp_path/'c2VjcmV0LXBheWxvYWQ='
        area.mkdir()
        (area/'AGENTS.md').symlink_to(target)
        changed = replace(grant, relative_alias='c2VjcmV0LXBheWxvYWQ=/AGENTS.md')
        from hashlib import sha256
        from agent.source_context_preview import rendered_preview
        body = rendered_preview(target.read_bytes(), changed.working_root, changed.relative_alias, target).encode()
        return replace(changed, preview_sha256=sha256(body).hexdigest(), preview_bytes=len(body)), ()
    grant, secrets = {'marker':lambda: (grant, ('truncated AGENTS.md',)), 'encoded_path':encoded_path,
                      'source_cap':lambda: (replace(grant, preview_bytes=262145), ())}[case]()
    text = verify_preview(grant)[0].decode() if case != 'source_cap' else 'forged preview'
    with pytest.raises(EgressBlocked):
        firewall(tmp_path/'egress', policy_digest='policy-1', exact_secret_values=secrets).authorize(
            preview_request(grant, text), _route(), grants=(grant,))
