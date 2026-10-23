"""Internal observation of independently derived native correlation identifiers.

These records convey no egress permission, source grant or result-content trust.
Only the actual owner execution and dispatcher bind them; server metadata never
constructs an owner. No MCP transport, persistence or live state is accessed.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
from hashlib import sha256
import json
from threading import RLock


_OWNER = ContextVar("native_metadata_owner", default=None)
_LOCK = RLock()
_OPERATIONS = {"a2a_policy", "a2a_submit", "a2a_read", "a2a_ack"}
_TASK_FIELDS = {"work_item_id", "project_id", "task_class", "commit", "question", "sources", "evidence"}


def _observation_only(function):
    @wraps(function)
    def observe(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:  # health: allow BLE001 -- Optional observation must preserve tool outcomes without logging private exception data.
            return None  # Observation cannot change application execution outcomes.
    return observe


@dataclass(frozen=True)
class _Owner:
    authority: object
    policy: object
    source: object
    profile_id: str
    logical_session_id: str
    durable_session_id: str
    overlay_scope: str
    manifest_sha256: str
    names: tuple[str, ...]
    entries: tuple[object, ...]

    @property
    def key(self):
        return (self.profile_id, self.logical_session_id, self.durable_session_id,
                self.overlay_scope, self.manifest_sha256, tuple(id(entry) for entry in self.entries))


@dataclass(frozen=True)
class NativeMetadataCapture:
    profile_id: str
    logical_session_id: str
    durable_session_id: str
    owner_key: tuple
    turn_id: str
    execution_api_request_id: str
    policy_digest: str
    call_id: str
    tool_name: str
    entry: object
    argument_sha256: str
    result_sha256: str
    request_id: str
    event_id: str
    presentation_sha256: str | None = None
    next_provider_request_id: str | None = None
    expected: object = None
    recipient_digest: str | None = None
    registry_entries: tuple[object, ...] = ()


def argument_digest(arguments):
    """Snapshot actual call arguments; malformed values cannot acquire a record."""
    try:
        raw = json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return sha256(raw.encode()).hexdigest()
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        return None


def native_metadata_argument_digest(agent, name, arguments):
    """Ordinary tools do not serialize arguments for this optional observer."""
    try:
        owner = _current_owner(agent)
        if owner is None or name not in owner.names or name.rsplit("__", 1)[-1] not in {"a2a_submit", "a2a_read", "a2a_ack"}:
            return None
        return argument_digest(arguments)
    except Exception:  # health: allow BLE001 -- Optional observation must preserve tool outcomes without logging private exception data.
        return None


def _request_identity(agent):
    return tuple(str(getattr(agent, key, "") or "") for key in (
        "_current_turn_id", "_current_api_request_id", "_llm_egress_policy_digest"))


def _owner_for_execution(authority, policy, source, durable_session_id):
    from gateway.config import Platform
    from gateway.session_local import LocalSessionAdapter
    from gateway.session_local_mcp import _direct_editor_names, session_scope
    from hermes_constants import hermes_home_key
    from tools.registry import current_session_tool_scope, registry

    if (authority is None or policy is None or source is None or policy.source != "acp"
            or source.platform != Platform.LOCAL or not durable_session_id):
        return None
    logical = source.chat_id
    live = authority.sessions.get(logical)
    adapter_ref = getattr(source, "_transport_adapter_ref", None)
    adapter = adapter_ref() if callable(adapter_ref) else None
    if (live is None or live.source is not source or getattr(live, "superseded", False)
            or getattr(live, "history_mount", False) or not isinstance(adapter, LocalSessionAdapter)
            or adapter.authority is not authority or adapter.policies.get(logical) is not policy
            or not adapter.authorize_source(source) or authority.profile_id != hermes_home_key()):
        return None
    data = json.loads(policy.editor_mcp_json or "null")
    names = _direct_editor_names(policy, data)
    if not isinstance(names, dict) or len(names) != 4:
        return None
    operations = [name.rsplit("__", 1) for name in names]
    if (any(len(parts) != 2 for parts in operations)
            or len({parts[0] for parts in operations}) != 1
            or {parts[1] for parts in operations} != _OPERATIONS):
        return None
    scope = session_scope(authority, logical)
    if data["scope"] != scope or current_session_tool_scope() != scope:
        return None
    ordered_names = tuple(sorted(names))
    entries = tuple(registry.get_entry(name) for name in ordered_names)
    if any(entry is None for entry in entries):
        return None
    return _Owner(authority, policy, source, authority.profile_id, logical,
                  durable_session_id, scope, sha256(policy.editor_mcp_json.encode()).hexdigest(),
                  ordered_names, entries)


@contextmanager
def native_metadata_execution_scope(authority, policy, source, durable_session_id):
    """Bind only an existing exact owner route; ordinary/nested scopes clear ambient authority."""
    try:
        owner = _owner_for_execution(authority, policy, source, durable_session_id)
    except Exception:  # health: allow BLE001 -- Optional observation must preserve tool outcomes without logging private exception data.
        owner = None
    token = _OWNER.set(owner)
    try:
        yield
    finally:
        _OWNER.reset(token)


def _current_owner(agent):
    owner = _OWNER.get()
    if owner is None or getattr(agent, "session_id", None) != owner.durable_session_id:
        return None
    try:
        current = _owner_for_execution(owner.authority, owner.policy, owner.source,
                                       owner.durable_session_id)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return owner if current is not None and current.key == owner.key else None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate metadata key")
        result[key] = value
    return result


def _remember(agent, owner, name, call_id, entry, arguments_digest, result, expected, identities, facts, recipient):
    if not all(identities) or not isinstance(call_id, str) or not 0 < len(call_id.encode()) <= 256:
        return
    record = NativeMetadataCapture(owner.profile_id, owner.logical_session_id,
        owner.durable_session_id, owner.key, *identities, call_id, name, entry,
        arguments_digest, sha256(result.encode()).hexdigest(), *expected,
        expected=facts,
        recipient_digest=recipient, registry_entries=owner.entries)
    with _LOCK:
        store = _owner_store(agent, owner)
        records = store[1]
        key = (*identities, call_id)
        if key in records:
            records[key] = None  # Duplicate dispatch cannot overwrite a prior observation.
        else:
            records[key] = record


def capture_key(record):
    return (record.turn_id, record.execution_api_request_id, record.policy_digest, record.call_id)


def _owner_store(agent, owner):
    """Session-owned observations survive an ordinary agent cache miss, not owner restart."""
    live = owner.authority.sessions[owner.logical_session_id]
    store = getattr(live, "_native_metadata_captures", None)
    if store is None or store[0] != owner.key:
        store = (owner.key, {})
        live._native_metadata_captures = store
    agent._native_metadata_captures = store  # Inspection alias, never proof authority.
    return store


def execute_with_native_metadata_capture(agent, name, call_id, original_digest, execute, arguments):
    """Observe one real dispatch; exceptions, args and returned bytes remain unchanged."""
    from tools.registry import registry

    try:
        owner = _current_owner(agent) if original_digest is not None else None
        eligible = owner is not None and name in owner.names and name.rsplit("__", 1)[-1] in {"a2a_submit", "a2a_read", "a2a_ack"}
        entry = registry.get_entry(name) if eligible else None
        digest = argument_digest(arguments) if eligible else None
        identities = _request_identity(agent) if eligible else ()
        recipient = _captured_recipient(agent, identities) if eligible else None
        eligible = eligible and entry is not None and digest is not None and digest == original_digest
        facts_before = _arguments_facts(owner, agent, name, arguments) if eligible else None
        eligible = eligible and facts_before is not None
    except Exception:  # health: allow BLE001 -- Optional observation must preserve tool outcomes without logging private exception data.
        eligible = False
    result = execute(arguments)
    try:
        if (not eligible or _current_owner(agent) is not owner or registry.get_entry(name) is not entry
                or argument_digest(arguments) != digest or _request_identity(agent) != identities
                or _captured_recipient(agent, identities) != recipient
                or _arguments_facts(owner, agent, name, arguments) != facts_before):
            return result
        facts = _expected_operation(owner, agent, name, arguments, result, facts=facts_before)
        if facts is not None:
            expected = (facts.request_id, facts.event_id)
            _remember(agent, owner, name, call_id, entry, digest, result, expected, identities, facts, recipient)
    except Exception:  # health: allow BLE001 -- Optional observation must preserve tool outcomes without logging private exception data.
        return result
    return result


def captured_native_metadata(agent):
    """Inspection only: records are NOT accepted by the egress classifier or firewall."""
    with _LOCK:
        owner = _current_owner(agent)
        store = _owner_store(agent, owner) if owner else getattr(agent, "_native_metadata_captures", None)
        return tuple(record for record in store[1].values() if record is not None) if store else ()


@_observation_only
def settle_native_metadata_execution(agent, call_id, result, *, blocked, dispatched):
    """Outer middleware may replace/block a result after the inner call ran."""
    with _LOCK:
        store = getattr(agent, "_native_metadata_captures", None)
        if store is None:
            return
        for key, record in tuple(store[1].items()):
            if record is not None and record.call_id == call_id:
                if (blocked or not dispatched or not isinstance(result, str)
                        or sha256(result.encode()).hexdigest() != record.result_sha256):
                    store[1][key] = None


@_observation_only
def commit_native_metadata_presentation(agent, call_id, raw_result, content, *, blocked, is_error, committed):
    """Record exact final presentation only after canonical append/flush succeeds."""
    from agent.source_provenance import following_api_request_id
    from agent.tool_dispatch_helpers import _maybe_wrap_untrusted

    owner = _current_owner(agent)
    with _LOCK:
        store = getattr(agent, "_native_metadata_captures", None)
        if store is None:
            return
        for key, record in tuple(store[1].items()):
            if record is None or record.call_id != call_id:
                continue
            following = following_api_request_id(record.execution_api_request_id, record.turn_id)
            expected = _maybe_wrap_untrusted(record.tool_name, raw_result) if isinstance(raw_result, str) else None
            valid = (owner is not None and owner.key == record.owner_key and committed
                     and _request_identity(agent) == (record.turn_id, record.execution_api_request_id,
                                                       record.policy_digest)
                     and not blocked and not is_error and following
                     and isinstance(content, str) and content == expected
                     and sha256(raw_result.encode()).hexdigest() == record.result_sha256)
            store[1][key] = replace(record, presentation_sha256=sha256(content.encode()).hexdigest(),
                                   next_provider_request_id=following) if valid else None


def _expected_facts(owner, arguments):
    from agent.native_metadata_derivation import expected_submit_ids
    return expected_submit_ids(logical_session_id=owner.logical_session_id,
        target=arguments["target"], work_item_id=arguments["task"]["work_item_id"])


def _captured_recipient(agent, identities):
    binding = getattr(agent, "_native_metadata_authorized_recipient", None)
    return binding[1] if binding and binding[0] == (agent.session_id, *identities) else None


def _arguments_facts(owner, agent, name, arguments):
    from agent.native_metadata_egress import _validated_store
    operation = name.rsplit("__", 1)[-1]
    if operation == "a2a_submit":
        if (type(arguments) is not dict or set(arguments) != {"target", "task"}
                or type(arguments["task"]) is not dict or set(arguments["task"]) != _TASK_FIELDS):
            return None
        return _expected_facts(owner, arguments)
    if type(arguments) is not dict:
        return None
    keys = {"request_id"} if operation == "a2a_read" else {"request_id", "event_id", "reply_sha256"}
    if set(arguments) != keys:
        return None
    with _LOCK:
        current = _owner_store(agent, owner)[1]
        priors = [record for record in _validated_store(owner).values()
            if record.request_id == arguments.get("request_id") and record.owner_key == owner.key
            and record.policy_digest == _request_identity(agent)[2]
            and record.recipient_digest == _captured_recipient(agent, _request_identity(agent))
            and current.get(capture_key(record)) is record]
    facts = {record.expected for record in priors}
    if len(facts) != 1:
        return None
    expected = facts.pop()
    if operation == "a2a_ack" and arguments.get("event_id") != expected.event_id:
        return None
    return expected


def metadata_byte_limit(agent):
    """Reuse the existing aggregate sanitized-content policy, never a new task cap."""
    default = 32768 if getattr(agent, "_llm_egress_aux_task", "") == "vision" else 262144
    return int(getattr(agent, "_llm_egress_max_sanitized_bytes", default))


def _expected_operation(owner, agent, name, arguments, result, *, facts=None):
    from agent.native_metadata_derivation import verify_metadata_presentation
    operation = name.rsplit("__", 1)[-1]
    facts = facts or _arguments_facts(owner, agent, name, arguments)
    limit = metadata_byte_limit(agent)
    if (facts is None or not isinstance(result, str) or limit <= 0
            or len(result) > limit or len(result.encode()) > limit):
        return None
    if operation == "a2a_ack":
        outer = json.loads(result, object_pairs_hook=_unique_object)
        payload = json.loads(outer["result"], object_pairs_hook=_unique_object) if set(outer) == {"result"} else None
        return facts if type(payload) is dict and set(payload) == {"acknowledged"} and type(payload["acknowledged"]) is bool else None
    digest = sha256(result.encode()).hexdigest()
    return facts if verify_metadata_presentation(operation=operation, expected=facts,
        text=result, presentation_sha256=digest, max_bytes=limit) else None
