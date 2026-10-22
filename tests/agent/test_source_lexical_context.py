"""Source suffix context stays exact without repeatedly scanning growing prefixes."""
import signal

import pytest

from agent.llm_egress_firewall import _source_text_for_base64_scan


@pytest.mark.skipif(not hasattr(signal, 'SIGALRM'), reason='native POSIX observation deadline')
def test_long_prose_line_keeps_sensitive_token_visible_within_existing_budget():
    def expired(*_args):
        raise TimeoutError('source lexical scan exceeded 15 seconds')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(15)
    try:
        masked = _source_text_for_base64_scan('source guidance. '*16000+' c2VjcmV0LXBheWxvYWQ=', allow_fixed_source_literals=True)
        assert 'c2VjcmV0LXBheWxvYWQ=' in masked
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def legacy_context(match, text):
    import re
    line_start = text.rfind('\n', 0, match.start())+1
    line_end = text.find('\n', match.end())
    if line_end < 0:
        line_end = len(text)
    before = text[line_start:match.start()]
    after = text[match.end():line_end]
    if before.rstrip().endswith(("'", '"')):
        return False
    if re.search(r'\b(?:def|class)\s+$', before):
        return True
    assignment = not before and re.match(r'\s*=', after) is not None
    return (not before.rstrip().endswith(('.', "'", '"'))
            and (assignment or (bool(before) and before[-1] in ' \t([{,:;'))
            and re.match(r'\s*(?:[=,.;:)(\]}])', after) is not None)


def test_position_context_matches_legacy_unicode_boundaries_and_delimiters():
    import re
    from itertools import product
    from agent.source_lexical_context import SourceLexicalContext
    prefixes = ['', 'def ', 'class\t', 'nodef ', 'édef ', '☃def ', "' ", '. ', 'key=', '(', '\n', '\r', 'def\v', 'class\f', 'def\u0085', 'class\u2028', 'def\u2029']
    suffixes = ['', '=', ' =', ' ;', ')', ']', '}', '\n=', '\r=', '\v=', '\f;', '\u0085)', '\u2028:', '\u2029.']
    for prefix, suffix in product(prefixes, suffixes):
        text = prefix+'PAYLOAD'+suffix
        match = re.search('PAYLOAD', text)
        assert bool(SourceLexicalContext(text).identifier_context(match)) == bool(legacy_context(match, text))


@pytest.mark.parametrize('text, visible', [('value = "c2VjcmV0LXBheWxvYWQ="', 'c2VjcmV0LXBheWxvYWQ='),
                                          ('notdef PAYLOAD', 'PAYLOAD'),
                                          ('# /Users/private-person/projects/private-project', '/Users/private-person/projects/private-project')])
def test_complete_masking_keeps_opaque_and_private_material_visible(text, visible):
    assert visible in _source_text_for_base64_scan(text, allow_fixed_source_literals=True)
