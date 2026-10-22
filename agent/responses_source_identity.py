"""In-process correspondence from actual Responses formatting, never source authority.

Only exact retained output occurrences carry the original dispatcher label. The
source registry still verifies producer, owner, request, policy, bytes and replay.
JSON serialization intentionally drops this transport-only correspondence.
"""
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json

_KEYS = ('request_id', 'source_grant_digests', 'content_sha256',
         'presentation_kind', 'read_result_binding_id')
_IDENTITY = '_responses_source_identity'
_SEAL = object()


def _hash(text):
    return sha256(text.encode('utf-8')).hexdigest()


def _metadata_hash(metadata):
    return _hash(json.dumps({key: metadata[key] for key in _KEYS if key in metadata},
                            sort_keys=True, separators=(',', ':')))


@dataclass(frozen=True)
class _Occurrence:
    original_call_id: str
    wire_call_id: str
    content_sha256: str
    metadata_sha256: str
    seal: object

    def matches(self, item):
        return (self.seal is _SEAL and isinstance(item, Mapping)
                and item.get('type') == 'function_call_output'
                and item.get('call_id') == self.wire_call_id
                and isinstance(item.get('output'), str)
                and _hash(item['output']) == self.content_sha256)


class _FormattedInput(list):
    def __init__(self, items, bindings):
        super().__init__(items)
        self.bindings = tuple(bindings)


def bind_formatted_outputs(items, source_pairs):
    """Called by the formatter after pruning, using actual emitted item identities."""
    sources = {id(item): message for item, message in source_pairs}
    counts = Counter(item.get('call_id') for item in items
                     if isinstance(item, dict) and item.get('type') == 'function_call_output')
    bindings = []
    for position, item in enumerate(items):
        source = sources.get(id(item))
        if source is None or item.get('type') != 'function_call_output':
            continue
        message = source
        metadata = message.get('_source_provenance')
        content = message.get('content')
        original = message.get('tool_call_id')
        if (not isinstance(metadata, dict) or not isinstance(content, str)
                or not isinstance(original, str) or item.get('output') != content
                or metadata.get('content_sha256') != _hash(content)
                or counts[item.get('call_id')] != 1):
            continue
        occurrence = _Occurrence(original, item['call_id'], _hash(content),
                                 _metadata_hash(metadata), _SEAL)
        bindings.append((position, occurrence))
    return _FormattedInput(items, bindings) if bindings else items


def carry_normalized_outputs(original, normalized, positions):
    """Retain correspondence only through exact output-preserving normalization."""
    if not isinstance(original, _FormattedInput):
        return normalized
    bindings = []
    for old_position, occurrence in original.bindings:
        new_position = positions.get(old_position)
        if (new_position is not None and occurrence.matches(original[old_position])
                and occurrence.matches(normalized[new_position])):
            bindings.append((new_position, occurrence))
    return _FormattedInput(normalized, bindings)


def restore_responses_sources(items, sidecar, consumed):
    """Restore only unique exact envelopes, independent of transport message filtering.

    The sidecar is captured before system extraction and provider message
    normalization. Its chat index cannot be compared with a formatter input
    index. Full envelope identity and actual dispatcher label must match once;
    the sealed final output position, wire label and bytes remain exact.
    """
    if not isinstance(items, _FormattedInput):
        return None
    result = list(items)
    counts = Counter(item.get('call_id') for item in items
                     if isinstance(item, Mapping) and item.get('type') == 'function_call_output')
    positions = Counter(position for position, _ in items.bindings)
    for position, occurrence in items.bindings:
        if (not 0 <= position < len(items) or positions[position] != 1
                or not occurrence.matches(items[position])
                or counts[occurrence.wire_call_id] != 1):
            continue
        matches = [(index, entry) for index, entry in enumerate(sidecar)
                   if index not in consumed and isinstance(entry, Mapping)
                   and entry.get('tool_call_id') == occurrence.original_call_id
                   and _metadata_hash(entry) == occurrence.metadata_sha256]
        if len(matches) != 1:
            continue
        index, entry = matches[0]
        metadata = {key: entry[key] for key in _KEYS if key in entry}
        metadata[_IDENTITY] = occurrence
        result[position] = {**items[position], '_source_provenance': metadata}
        consumed.add(index)
    return result


def original_observed_call(value, metadata, observed):
    occurrence = metadata.get(_IDENTITY)
    if (isinstance(occurrence, _Occurrence) and occurrence.matches(value)
            and _metadata_hash(metadata) == occurrence.metadata_sha256):
        return occurrence.original_call_id
    return observed
