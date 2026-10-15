"""Recover exact read-result source proofs after native Messages conversion."""
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from agent.llm_egress_classifier import _structured_tool_output_text


def restore_native_source_sidecar(messages: Any, sidecar: list, consumed: set[int]) -> Any:
    """Bind only a unique result block and unique proof with matching ID and bytes."""
    if not isinstance(messages, list):
        return messages
    blocks = {}
    for message_index, message in enumerate(messages):
        if not isinstance(message, Mapping) or message.get('role') != 'user':
            continue
        content = message.get('content')
        if isinstance(content, list):
            for block_index, block in enumerate(content):
                if isinstance(block, Mapping) and block.get('type') == 'tool_result':
                    blocks[message_index, block_index] = block
    matches: dict[tuple[int, int], list[int]] = {}
    for entry_index, entry in enumerate(sidecar):
        if entry_index in consumed or not isinstance(entry, Mapping):
            continue
        call_id = entry.get('tool_call_id')
        if not isinstance(call_id, str):
            continue
        candidates = [key for key, block in blocks.items() if block.get('tool_use_id') == call_id]
        if len(candidates) != 1:
            continue
        key = candidates[0]
        content = blocks[key].get('content')
        text = content if isinstance(content, str) else _structured_tool_output_text(content)
        if isinstance(text, str) and sha256(text.encode('utf-8')).hexdigest() == entry.get('content_sha256'):
            matches.setdefault(key, []).append(entry_index)
    restored = list(messages)
    for (message_index, block_index), entries in matches.items():
        if len(entries) != 1:
            continue
        entry_index = entries[0]
        entry = sidecar[entry_index]
        message = dict(restored[message_index])
        content = list(message['content'])
        block = dict(content[block_index])
        block['_source_provenance'] = {name: entry[name] for name in (
            'request_id', 'source_grant_digests', 'content_sha256', 'presentation_kind') if name in entry}
        content[block_index] = block
        message['content'] = content
        restored[message_index] = message
        consumed.add(entry_index)
    return restored
