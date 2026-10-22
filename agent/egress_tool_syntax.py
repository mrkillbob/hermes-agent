"""Final validation for bounded public tool syntax, independent of source grants."""


def validate_tool_syntax(text: str, syntax_kind: str) -> str:
    from agent.llm_egress_firewall import (
        _VALIDATED_TOOL_SYNTAX, _contains_secret, _contains_private_absolute_path,
    )
    from agent.source_context_diagnostic import (
        CAPACITY_KIND, NOTICE_KIND, blocked_notice_filename, capacity_notice_filename,
    )

    grammar = _VALIDATED_TOOL_SYNTAX.get(syntax_kind)
    valid = (blocked_notice_filename(text) is not None if syntax_kind == NOTICE_KIND
             else capacity_notice_filename(text) is not None if syntax_kind == CAPACITY_KIND
             else isinstance(text, str) and grammar is not None and grammar.fullmatch(text) is not None)
    if not valid or _contains_secret(text) or _contains_private_absolute_path(text):
        raise ValueError('invalid_tool_syntax_segment')
    return text
