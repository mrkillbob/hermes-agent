"""Type fixed words only inside matching, package-owned builtin schema prose.

Do not import tool modules or trust the mutable tool registry at authorization.
Only the closed source bindings below supply fragments; all remaining bytes
retain the ordinary secret, path and encoding scans.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Mapping

from agent.llm_egress_firewall import (
    GeneratedContextSegment, OutboundText, SanitizedSegment, ValidatedToolSyntaxSegment,
)

_BUILTINS = {
    'skill_manage': ('skill_manager_tool.py', 'SKILL_MANAGE_SCHEMA', ('YAML',)),
    'write_file': ('file_tools.py', 'WRITE_FILE_SCHEMA', ('EXISTING',)),
    'delegate_task': ('delegate_tool.py', '_DESCRIPTION_DELIVERY', ('YOUR', 'TURN')),
    'tool_search': ('tool_search.py', '_CONNECTIONS_HINT', ('connectors__',)),
}


@lru_cache(maxsize=4)
def _source_fragments(name: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    filename, binding, words = _BUILTINS[name]
    path = Path(__file__).resolve().parent.parent / 'tools' / filename
    try:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    except (OSError, SyntaxError, UnicodeError):
        return ()
    fragments = []
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or not any(
            isinstance(target, ast.Name) and target.id == binding for target in statement.targets
        ):
            continue
        for node in ast.walk(statement.value):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            # delegate_task substitutes its closed delivery phrase before the
            # fixed guidance. Match the unchanged suffix, never dynamic text.
            text = node.value.split('{delivery}')[-1]
            present = tuple(word for word in words if re.search(r'\b' + re.escape(word) + r'\b', text))
            if present and len(text) > max(map(len, present)):
                fragments.append((text, present))
    return tuple(fragments)


def _description(text: Any, typed: Any, fragments) -> Any:
    safe_types = (SanitizedSegment, GeneratedContextSegment, ValidatedToolSyntaxSegment)
    parts = typed.segments if isinstance(typed, OutboundText) else (typed,)
    if not isinstance(text, str) or not all(isinstance(part, safe_types) for part in parts):
        return typed
    if ''.join(part.text for part in parts) != text:
        return typed  # Never resurrect anything redacted by the classifier.
    spans = set()
    for fragment, words in fragments:
        start = text.find(fragment)
        while start >= 0:
            for word in words:
                for match in re.finditer(r'\b' + re.escape(word) + r'\b', fragment):
                    spans.add((start + match.start(), start + match.end()))
            start = text.find(fragment, start + len(fragment))
    if not spans:
        return typed
    output, cursor = [], 0
    for start, end in sorted(spans):
        if start < cursor:
            continue
        if start > cursor:
            output.append(SanitizedSegment(text[cursor:start]))
        output.append(ValidatedToolSyntaxSegment(text[start:end], 'builtin_schema_keyword'))
        cursor = end
    if cursor < len(text):
        output.append(SanitizedSegment(text[cursor:]))
    return OutboundText(tuple(output))


def _schema(raw: Any, typed: Any, fragments) -> Any:
    if isinstance(raw, Mapping) and isinstance(typed, Mapping):
        output = dict(typed)
        for key, child in raw.items():
            if key in typed:
                output[key] = (_description(child, typed[key], fragments) if key == 'description'
                               else _schema(child, typed[key], fragments))
        return output
    if isinstance(raw, list) and isinstance(typed, list) and len(raw) == len(typed):
        return [_schema(child, other, fragments) for child, other in zip(raw, typed)]
    return typed


def type_builtin_schema_words(body: Mapping[str, Any], typed_body: Mapping[str, Any]) -> Mapping[str, Any]:
    tools, typed_tools = body.get('tools'), typed_body.get('tools')
    if not isinstance(tools, list) or not isinstance(typed_tools, list) or len(tools) != len(typed_tools):
        return typed_body
    output = list(typed_tools)
    for index, (tool, typed) in enumerate(zip(tools, typed_tools)):
        if not isinstance(tool, Mapping) or tool.get('type') != 'function' or not isinstance(typed, Mapping):
            continue
        function = tool.get('function')
        if (isinstance(function, Mapping) and isinstance(function.get('name'), str)
                and function['name'] in _BUILTINS):
            output[index] = _schema(tool, typed, _source_fragments(function['name']))
    return {**typed_body, 'tools': output}
