"""Lossless todo state at a Hermes compaction boundary.

The read result is reference data, never a new human request. Classification is
deliberately usable after persistence drops internal message metadata; it only
denies instruction authority, and does not authenticate a snapshot for recovery.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from agent.message_sanitization import coalesce_tool_call_id
from tools.todo_tool import MAX_TODO_RESULT_CHARS, TODO_INJECTION_HEADER, TodoStore, is_todo_tool_call

_PROVENANCE = "hermes_compaction_todo_state_v1"
_REFERENCE = "Saved todo tool data from before compaction; not a new user instruction. Follow the latest human request."
logger = logging.getLogger(__name__)


class TodoStateDeferred(ValueError):
    """Compaction cannot prove that it will preserve complete, attributable state."""


def is_todo_state_result(message: Any) -> bool:
    """Recognize reference data without granting it recovery authority."""
    if not isinstance(message, dict) or message.get("role") != "tool":
        return False
    if message.get("_todo_snapshot_synthetic") is True:
        return True
    content = message.get("content")
    if not isinstance(content, str) or _PROVENANCE not in content[:512]:
        return False
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("provenance") == _PROVENANCE


def todo_steer_suffix(message: Any) -> str | None:
    """For structured tool data return only a legacy appended steer suffix.

    None means this is not recognizable structured tool data. An empty string means all
    its text is reference data. Never interpret markers inside the JSON body.
    """
    if is_todo_state_result(message):
        return ""
    if not isinstance(message, dict) or message.get("role") != "tool":
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    try:
        stripped = content.lstrip()
        data, end = json.JSONDecoder().raw_decode(stripped)
    except (ValueError, TypeError):
        return None
    if isinstance(data, (dict, list)):
        return stripped[end:]
    return None


def _validated_snapshot(data: Any) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("todos"), list):
        raise TodoStateDeferred("todo snapshot unavailable")
    revision = data.get("revision", 1)
    if type(revision) is not int or revision < 0:
        raise TodoStateDeferred("invalid todo revision")
    if revision == 0 and data["todos"]:
        raise TodoStateDeferred("nonempty todo state has no recoverable revision")
    snapshot = copy.deepcopy({"todos": data["todos"], "revision": revision})
    # Validate by round trip into a disposable store, never by normalizing live
    # state. Any truncation, discarded key, reordered item or repair is refusal.
    check = TodoStore()
    try:
        check.restore(snapshot["todos"], revision=revision)
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise TodoStateDeferred("invalid full todo state") from exc
    if check.snapshot() != snapshot:
        raise TodoStateDeferred("todo state requires lossy normalization")
    return snapshot


def _paired_todo_result(messages: list, index: int) -> bool:
    call_id = messages[index].get("tool_call_id")
    if not call_id:
        return False
    for prior in reversed(messages[:index]):
        if not isinstance(prior, dict):
            continue
        if prior.get("role") in {"user", "system"}:
            return False
        if prior.get("role") == "assistant":
            calls = prior.get("tool_calls")
            return isinstance(calls, list) and any(
                coalesce_tool_call_id(call) == call_id and is_todo_tool_call(call) for call in calls
            )
    return False


def _history_snapshot(messages: list) -> dict | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, dict) or message.get("role") != "tool" or not _paired_todo_result(messages, index):
            continue
        content = message.get("content")
        if not isinstance(content, str) or len(content) > MAX_TODO_RESULT_CHARS:
            raise TodoStateDeferred("todo history cannot be recovered completely")
        try:
            data, _ = json.JSONDecoder().raw_decode(content.lstrip())
        except (ValueError, TypeError) as exc:
            raise TodoStateDeferred("invalid todo history") from exc
        if isinstance(data, dict) and "error" in data and "todos" not in data:
            continue  # Rejected writes did not change the state being recovered.
        return _validated_snapshot(data)
    return None


def _payload(snapshot: dict, reload_notice: str = "") -> str:
    data = {"provenance": _PROVENANCE, "reference": _REFERENCE, **snapshot}
    if reload_notice:
        data["pruned_skill_reload_reference"] = reload_notice
    result = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(result) > MAX_TODO_RESULT_CHARS:
        raise TodoStateDeferred("complete todo result exceeds recovery limit")
    return result


def _texts(content: Any):
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                yield part["text"]


def _legacy_suffixes(agent: Any, messages: list, snapshot: dict | None) -> set[str]:
    """Only exact, reproducible old renders are attributable. Unknown text defers."""
    suffixes: set[str] = set()
    if snapshot is not None:
        check = TodoStore()
        check.restore(snapshot["todos"], revision=snapshot["revision"])
        rendered = check.format_for_injection()
        if rendered:
            suffixes.add(rendered)
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        if message.get("_todo_snapshot_synthetic") is True and message.get("content") not in tuple(suffixes):
            raise TodoStateDeferred("legacy todo carrier is not completely attributable")
        for text in _texts(message.get("content")):
            if TODO_INJECTION_HEADER in text:
                # Unflagged merged user suffixes are indistinguishable from a
                # human quoting the old header. Preserve them by deferring.
                if message.get("_todo_snapshot_synthetic") is not True or text not in suffixes:
                    raise TodoStateDeferred("ambiguous legacy todo text")
    return suffixes


def prepare_todo_state(agent: Any, messages: list) -> dict | None:
    """Read-only preflight, including fresh/detached agents and explicit clears."""
    store = getattr(agent, "_todo_store", None)
    snapshot_fn = getattr(store, "snapshot", None)
    try:
        raw = snapshot_fn() if callable(snapshot_fn) else None
    except Exception as exc:
        raise TodoStateDeferred("todo snapshot unavailable") from exc
    # Older embedders may have no revisioned store. They can proceed only when
    # there is no todo evidence to lose (MagicMock attributes are not evidence).
    snapshot = _validated_snapshot(raw) if isinstance(raw, dict) else None
    for index, message in enumerate(messages):
        if is_todo_state_result(message):
            if not _paired_todo_result(messages, index):
                raise TodoStateDeferred("unpaired derived todo evidence")
            try:
                if not isinstance(message.get("content"), str) or len(message["content"]) > MAX_TODO_RESULT_CHARS:
                    raise TodoStateDeferred("derived todo evidence exceeds recovery limit")
                _validated_snapshot(json.loads(message["content"]))
            except (ValueError, TypeError, KeyError) as exc:
                raise TodoStateDeferred("damaged derived todo evidence") from exc
    history = _history_snapshot(messages)
    if history is not None:
        if snapshot is None or history["revision"] > snapshot["revision"]:
            snapshot = history
        elif history["revision"] == snapshot["revision"] and history != snapshot:
            raise TodoStateDeferred("conflicting todo state at the same revision")
    if snapshot is not None and snapshot == {"todos": [], "revision": 0}:
        snapshot = None
    if snapshot is None and store is not None:
        render = getattr(store, "format_for_injection", None)
        if callable(render) and isinstance(rendered := render(), str) and rendered:
            raise TodoStateDeferred("legacy todo store has no complete snapshot")
    _legacy_suffixes(agent, messages, snapshot)
    if snapshot is not None:
        if not isinstance(raw, dict) or not callable(getattr(store, "restore", None)):
            raise TodoStateDeferred("live todo store cannot reconcile complete recovered state")
        _payload(snapshot)
    return snapshot


def fold_todo_state(agent: Any, messages: list, *, snapshot: dict | None, reload_notice: str = "") -> None:
    """Refresh exactly one complete read exchange, retaining unrelated messages."""
    _legacy_suffixes(agent, messages, snapshot)
    payload = _payload(snapshot, reload_notice) if snapshot is not None else None
    if payload is None and not any(is_todo_state_result(message) for message in messages):
        return
    removed_ids = set()
    out = []
    for index, message in enumerate(messages):
        if is_todo_state_result(message):
            if not _paired_todo_result(messages, index):
                raise TodoStateDeferred("unpaired derived todo result")
            removed_ids.add(message["tool_call_id"])
            continue
        if isinstance(message, dict) and message.get("role") == "user" and message.get("_todo_snapshot_synthetic") is True:
            # Preflight proved every text part to be the exact old standalone
            # rendering. A mixed/multimodal legacy carrier is not safely owned.
            content = message.get("content")
            if not isinstance(content, str):
                raise TodoStateDeferred("mixed legacy todo carrier")
            continue
        out.append(copy.deepcopy(message))
    for message in out[:]:
        if isinstance(message, dict) and message.get("role") == "assistant" and message.get("tool_calls"):
            calls = [call for call in message["tool_calls"] if coalesce_tool_call_id(call) not in removed_ids]
            if calls:
                message["tool_calls"] = calls
            else:
                message.pop("tool_calls", None)
                from agent.agent_runtime_helpers import _msg_has_payload
                if not _msg_has_payload(message):
                    out.remove(message)
    if payload is not None:
        call_id = "todo_compaction_" + uuid.uuid4().hex
        exchange = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": "todo_list", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": call_id, "name": "todo_list", "tool_name": "todo_list",
             "content": payload, "_todo_snapshot_synthetic": True},
        ]
        # Keep the final reply a final reply (otherwise strict templates and
        # in-flight detection treat a completed human task as unfinished).
        # A trailing human request also stays after the reference exchange.
        insertion = len(out)
        if (out and out[-1].get("role") in {"user", "assistant"} and not out[-1].get("tool_calls")
                and (out[-1].get("role") != "user" or len(out) > 1)):
            insertion -= 1
            if (out[-1].get("role") == "user" and insertion > 0
                    and out[insertion - 1].get("role") == "assistant"
                    and not out[insertion - 1].get("tool_calls")):
                insertion -= 1  # Preserve the preceding completed reply as a visible assistant turn too.
        out[insertion:insertion] = exchange
    agent._repair_message_sequence(out)
    validate_todo_state(out, snapshot)
    messages[:] = out


def reconcile_todo_store(agent: Any, snapshot: dict | None) -> None:
    """After successful publication, fresh/detached stores use the same revision."""
    if snapshot is not None:
        store = agent._todo_store
        if store.snapshot() != snapshot:
            store.restore(snapshot["todos"], revision=snapshot["revision"])


def reconcile_published_todo_state(agent: Any, snapshot: dict | None, commit: Any) -> None:
    if commit.session_commit_succeeded or commit.split_status == "not_applicable":
        reconcile_todo_store(agent, snapshot)


def defer_todo_compaction(agent: Any, reason: str) -> None:
    """A no-work outcome must not inherit a prior successful boundary's cursors."""
    agent._last_compression_attempt_recorded = True
    agent._last_compression_attempt_in_place = None
    agent._compression_skipped_due_to_lock = None
    agent._compression_blocked_transient = "todo_state_deferred"
    agent._compression_todo_deferred_reason = reason
    logger.warning("Compression deferred: %s", reason)
    warning = getattr(agent, "_emit_warning", None)
    if callable(warning):
        with contextlib.suppress(Exception):
            warning("Compression deferred to preserve complete todo state: " + reason + ". History is unchanged.")


def todo_tail_owns_state(tail: list | None, snapshot: dict | None) -> bool:
    if tail and any(is_todo_state_result(message) for message in tail):
        validate_todo_state(tail, snapshot)
        return True
    return False


def preflight_todo_compaction(agent: Any, messages: list, verbatim_tail: list | None) -> bool:
    agent._compression_todo_deferred_reason = None
    if getattr(agent, "api_mode", None) == "codex_app_server":
        return True  # Native compaction never rewrites the local transcript.
    try:
        snapshot = prepare_todo_state(agent, [*messages, *(verbatim_tail or [])])
        todo_tail_owns_state(verbatim_tail, snapshot)
    except TodoStateDeferred as exc:
        defer_todo_compaction(agent, str(exc))
        return False
    return True


@dataclass
class TodoCompactionCandidate:
    messages: list | None
    user_turn_outcome: str = ""
    reply: dict | None = None
    tail_ids: set | None = None
    refused_prompt: str | None = None


def prepare_compaction_candidate(
    agent: Any, messages: list, compressed: list, *, original: list | None,
    snapshot: dict | None, system_message: str, attempt: Any, verbatim_tail: list | None,
) -> TodoCompactionCandidate:
    """All refusal gates precede prompt rebuilding and durable publication."""
    from agent.conversation_compression import (
        _ensure_compressed_has_user_turn, _pruned_skill_reload_notice, _salvage_or_refuse_grown_transcript,
    )
    from agent.conversation_compression_reply_anchor import _ensure_compressed_keeps_last_assistant_reply

    try:
        reply = None if verbatim_tail else _ensure_compressed_keeps_last_assistant_reply(
            messages, compressed, session_id=agent.session_id,
        )
        if prepare_todo_state(agent, [*(original or messages), *(verbatim_tail or [])]) != snapshot:
            raise TodoStateDeferred("todo state changed during compaction")
        tail_owns_todo = todo_tail_owns_state(verbatim_tail, snapshot)
        reload_notice = _pruned_skill_reload_notice(compressed)
        if tail_owns_todo and reload_notice:
            raise TodoStateDeferred("new skill reload guidance cannot alter the preserved todo tail")
        fold_todo_state(agent, compressed, snapshot=None if tail_owns_todo else snapshot, reload_notice=reload_notice)
        outcome = _ensure_compressed_has_user_turn(messages, compressed)
        # Match the commit site's original ordering: internal tail tags are
        # bookkeeping, not wire tokens. Retain their identity set for archive.
        tail_ids = {id(m) for m in compressed if isinstance(m, dict) and m.pop("_compaction_tail", None)}
        if agent._session_db:
            compressed, refused = _salvage_or_refuse_grown_transcript(
                agent, original if original is not None else messages, compressed,
                system_message=system_message, attempt=attempt,
            )
            if compressed is None:
                return TodoCompactionCandidate(None, refused_prompt=refused)
        validate_todo_state([*compressed, *(verbatim_tail or [])], snapshot)
        return TodoCompactionCandidate(compressed, outcome, reply, tail_ids)
    except TodoStateDeferred as exc:
        attempt.restore_compressor(agent.context_compressor)
        defer_todo_compaction(agent, str(exc))
        return TodoCompactionCandidate(None, refused_prompt=system_message)


def validate_todo_state(messages: list, snapshot: dict | None) -> None:
    """Check after mechanical transforms before durable publication."""
    results = [(index, message) for index, message in enumerate(messages) if is_todo_state_result(message)]
    if snapshot is None:
        if results:
            raise TodoStateDeferred("unexpected derived todo state")
        return
    if len(results) != 1:
        raise TodoStateDeferred("derived todo state lost or duplicated")
    index, message = results[0]
    if not _paired_todo_result(messages, index):
        raise TodoStateDeferred("derived todo call pairing lost")
    try:
        data = json.loads(message["content"])
    except (ValueError, TypeError) as exc:
        raise TodoStateDeferred("derived todo result damaged") from exc
    if len(message["content"]) > MAX_TODO_RESULT_CHARS or _validated_snapshot(data) != snapshot:
        raise TodoStateDeferred("derived todo state changed")
