"""Oversized hints produce honest capacity notices without exporting unsupported previews."""
import pytest

from tests.agent.test_source_read_result_binding import authorize, produce


@pytest.mark.parametrize('shape', ['bytes', 'lines', 'single_line'])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_capacity_notice_preserves_actual_read_prefix_and_history(tmp_path, monkeypatch, shape, newline):
    text = {'bytes': ('source guidance. '*30 + newline)*600,
            'lines': ('source guidance.'+newline)*2001,
            'single_line': 'source guidance. '*16000}[shape]
    agent, messages, _ = produce(tmp_path, monkeypatch, count=1, guidance_override=text)
    wire = messages[0]['content']
    reason = 'line' if shape == 'lines' else 'byte'
    assert f'[UNAVAILABLE: AGENTS.md exceeds protected source {reason} limit.' in wire
    assert 'Use a supported bounded read_file slice or provide a bounded guidance file' in wire
    assert 'source guidance.' not in wire
    assert 'truncated AGENTS.md' not in wire
    for number in (2, 3, 4):
        authorized, receipt = authorize(agent, messages, number)
        assert authorized['messages'][0]['content'] == wire
        assert receipt.decision.source_grant_count == 1


@pytest.mark.parametrize('case', ['changed_bytes', 'registry_denied', 'policy_unavailable', 'escaping_alias', 'retargeted_alias'])
def test_capacity_notice_cannot_hide_failed_source_verification(tmp_path, monkeypatch, case):
    from agent import source_context_presentation, source_provenance
    from agent.llm_egress_firewall import EgressBlocked

    original = source_context_presentation.issue_context_read

    def intervening_change(path, raw, displayed, **kwargs):
        if case == 'changed_bytes':
            path.write_text('Changed source.\n')
        elif case in ('registry_denied', 'policy_unavailable'):
            def policy(_path):
                if case == 'policy_unavailable':
                    raise ValueError('policy unavailable')
                return 'denied'
            monkeypatch.setattr(source_provenance, 'get_read_block_error', policy)
        else:
            replacement = (tmp_path if case == 'escaping_alias' else path.parent) / 'replacement.md'
            replacement.write_bytes(raw)
            path.unlink()
            path.symlink_to(replacement)
        return original(path, raw, displayed, **kwargs)

    monkeypatch.setattr(source_context_presentation, 'issue_context_read', intervening_change)
    agent, messages, _ = produce(tmp_path, monkeypatch, count=1, guidance_override='source guidance. '*16000)
    assert '[UNAVAILABLE:' not in messages[0]['content']
    with pytest.raises(EgressBlocked):
        authorize(agent, messages)


@pytest.mark.parametrize('case', ['reason', 'wrapper', 'filename', 'tail', 'encoded_reason', 'duplicate_reason'])
def test_capacity_diagnostic_accepts_no_free_payload_slot(case):
    from agent.egress_tool_syntax import validate_tool_syntax
    text = ('[UNAVAILABLE: AGENTS.md exceeds protected source byte limit. '
            'No guidance preview was loaded. Use a supported bounded read_file slice or provide a bounded guidance file '
            'before relying on these instructions.]')
    text = {'reason':text.replace('byte limit', 'policy limit'),
            'wrapper':text.replace('No guidance preview was loaded.', 'Guidance was approved.'),
            'filename':text.replace('AGENTS.md', 'private.md'),
            'tail':text+' arbitrary tail',
            'encoded_reason':text.replace('byte limit', 'c2VjcmV0LXBheWxvYWQ= limit'),
            'duplicate_reason':text.replace('byte limit', 'byte, line limit')}[case]
    with pytest.raises(ValueError):
        validate_tool_syntax(text, 'context_capacity_notice_v1')


@pytest.mark.parametrize('secret', ['AGENTS.md]\n[UNAVAILABLE: AGENTS.md', 'Use a supported bounded read_file slice or provide a bounded guidance file',
                                  '[UNAVAILABLE: AGENTS.md exceeds protected source byte limit. No guidance preview was loaded. Use a supported bounded read_file slice or provide a bounded guidance file before relying on these instructions.]'])
def test_capacity_diagnostic_never_overrides_configured_exact_secrets(tmp_path, monkeypatch, secret):
    from hermes_cli import env_loader
    from agent.llm_egress_firewall import EgressBlocked
    agent, messages, _ = produce(tmp_path, monkeypatch, count=1, guidance_override='source guidance. '*16000)
    home = tmp_path/'profile-home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setitem(env_loader._SECRET_SOURCE_VALUES_BY_HOME, str(home.resolve()), {'EXTERNAL_VALUE':secret})
    with pytest.raises(EgressBlocked) as caught:
        authorize(agent, messages)
    assert 'exact_secret_detected' in caught.value.decision.reason_codes


@pytest.mark.parametrize('text', ['Preserve guidance 0.\n', 'source guidance.\n'*2000])
def test_supported_guidance_keeps_original_wire_and_whole_source_proof(tmp_path, monkeypatch, text):
    agent, messages, _ = produce(tmp_path, monkeypatch, count=1, guidance_override=text)
    wire = messages[0]['content']
    assert '[UNAVAILABLE:' not in wire
    assert 'source guidance.' in wire or 'Preserve guidance 0.' in wire
    for number in (2, 3):
        authorized, receipt = authorize(agent, messages, number)
        assert authorized['messages'][0]['content'] == wire
        assert receipt.decision.source_grant_count == 2


def test_capacity_notice_cannot_replace_missing_prefix_authority(tmp_path, monkeypatch):
    from agent.llm_egress_firewall import EgressBlocked
    agent, messages, _ = produce(tmp_path, monkeypatch, count=1, guidance_override='source guidance. '*16000)
    messages[0]['_source_provenance']['source_grant_digests'] = []
    with pytest.raises(EgressBlocked):
        authorize(agent, messages)


def test_closed_capacity_notice_does_not_retype_generic_untrusted_content(tmp_path, monkeypatch):
    from agent.llm_egress_firewall import EgressBlocked
    agent, messages, _ = produce(tmp_path, monkeypatch, count=1, guidance_override='source guidance. '*16000)
    notice = messages[0]['content'].split(']\n', 1)[1]
    messages[0]['content'] = notice
    messages[0].pop('_source_provenance')
    with pytest.raises(EgressBlocked):
        authorize(agent, messages)
