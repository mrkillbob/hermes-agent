"""Bounded producer identities for exact read results, independent of source authority."""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import uuid

from agent.llm_egress_firewall import SourceGrant, source_grant_digest

READ_RESULT_ID = 'read_result_binding_id'
OBSERVED_CALL_ID = '_observed_tool_call_id'


@dataclass(frozen=True)
class ProducerRead:
    grant: SourceGrant
    call_id: str
    prefix_sha256: str
    record_id: str
    content_sha256: str | None = None
    validated: bool = False

    @property
    def key(self):
        grant = self.grant
        return (grant.session_id, grant.turn_id, grant.request_id, grant.policy_digest, self.call_id)


class ReadResultBindings:
    """All methods run under the owning source registry's lock; no raw bytes retained."""

    def __init__(self):
        self._records = {}
        self._calls = {}

    def issue(self, grant, call_id, prefix_sha256, count_limit, byte_limit):
        if not isinstance(call_id, str) or not 0 < len(call_id.encode('utf-8')) <= 256:
            return
        if (not isinstance(prefix_sha256, str) or len(prefix_sha256) != 64
                or any(char not in '0123456789abcdef' for char in prefix_sha256)):
            return
        record = ProducerRead(grant, call_id, prefix_sha256, uuid.uuid4().hex)
        if record.key in self._calls:
            self._records.pop(self._calls[record.key], None)
            self._calls[record.key] = None  # Ambiguous duplicate producers cannot overwrite one another.
            return
        current = [key for key in self._calls if key[2] == grant.request_id]
        byte_count = sum(len(key[4].encode('utf-8')) + 160 for key in (*current, record.key))
        if len(current) >= count_limit or byte_count > byte_limit:
            return
        self._calls[record.key] = record.record_id
        self._records[record.record_id] = record

    def commit(self, owner, call_id, content):
        if not isinstance(content, str) or not isinstance(call_id, str):
            return None
        key = (*owner, call_id)
        record = self._records.get(self._calls.get(key))
        if record is None:
            return None
        try:
            _, end = json.JSONDecoder().raw_decode(content)
        except (TypeError, ValueError):
            return None
        if sha256(content[:end].encode('utf-8')).hexdigest() != record.prefix_sha256:
            return None
        digest = sha256(content.encode('utf-8')).hexdigest()
        if record.content_sha256 is not None and record.content_sha256 != digest:
            self._records.pop(record.record_id, None)
            self._calls[key] = None
            return None
        self._records[record.record_id] = replace(record, content_sha256=digest)
        return record.record_id

    def lookup(self, record_id, original_request_id, call_id, content, identity, current_grants):
        from agent.source_provenance import _is_later_request

        if not isinstance(record_id, str):
            return None
        record = self._records.get(record_id)
        if record is None or record.call_id != call_id or record.grant.request_id != original_request_id:
            return None
        grant = record.grant
        owner = (identity['session_id'], identity['turn_id'], identity['policy_digest'])
        if ((grant.session_id, grant.turn_id, grant.policy_digest) != owner
                or record.content_sha256 != sha256(content.encode('utf-8')).hexdigest()):
            return None
        if identity['request_id'] == original_request_id:
            return record if grant in current_grants else None
        if record.validated and _is_later_request(original_request_id, identity['request_id'], grant.turn_id):
            return record
        return None

    def remember_validated(self, selections, used_grants):
        for record_id, request_id, digest, wire_hash in selections:
            record = self._records.get(record_id)
            current = used_grants.get(digest)
            if record is None or current is None or record.content_sha256 != wire_hash:
                continue
            original = record.grant
            if ((current.session_id, current.turn_id, current.policy_digest, current.request_id)
                    == (original.session_id, original.turn_id, original.policy_digest, request_id)
                    and (current.canonical_path, current.line_start, current.line_end)
                    == (original.canonical_path, original.line_start, original.line_end)):
                self._records[record_id] = replace(record, validated=True)

    def clear_request(self, request_id):
        for key, record_id in tuple(self._calls.items()):
            record = self._records.get(record_id)
            if key[2] == request_id and (record is None or not record.validated):
                self._calls.pop(key, None)
                self._records.pop(record_id, None)

    def clear_turn(self, turn_id):
        for key, record_id in tuple(self._calls.items()):
            if key[1] == turn_id:
                self._calls.pop(key, None)
                self._records.pop(record_id, None)


class SourceReadSelection(dict):
    """Source grants plus exact complete read results selected by this authorization."""

    def __init__(self):
        super().__init__()
        self.result_selections = []
        self._calls = set()

    def select_result(self, record, request_id, digest):
        if record.key in self._calls:
            return False
        self._calls.add(record.key)
        self.result_selections.append((record.record_id, request_id, digest, record.content_sha256))
        return True


def observed_source_metadata(value):
    """Call labels come from the actual provider result shape, never its sidecar."""
    metadata = value.get('_source_provenance')
    if not isinstance(metadata, Mapping):
        return metadata
    call_id = (value.get('tool_use_id') if value.get('type') == 'tool_result'
               else value.get('tool_call_id') or value.get('call_id'))
    return {**metadata, OBSERVED_CALL_ID: call_id}


def select_producer_read(text, metadata, registry, identity):
    if registry is None:
        return None
    record = registry.read_result_binding(
        metadata.get(READ_RESULT_ID), metadata.get('request_id'), metadata.get(OBSERVED_CALL_ID),
        text, identity,
    )
    allowed = metadata.get('source_grant_digests')
    if (record is None or not isinstance(allowed, (tuple, list))
            or source_grant_digest(record.grant) not in allowed):
        return None
    return record


def select_complete_result(used_grants, record, request_id, digest):
    return isinstance(used_grants, SourceReadSelection) and used_grants.select_result(record, request_id, digest)


def remember_validated_sources(registry, selection):
    """Called only after final firewall authorization, never from partial parsing."""
    from agent.source_provenance import SourceProvenanceRegistry

    if isinstance(registry, SourceProvenanceRegistry):
        registry.remember_validated_presentations(tuple(selection.values()))
        registry.remember_validated_read_results(selection.result_selections, selection)
