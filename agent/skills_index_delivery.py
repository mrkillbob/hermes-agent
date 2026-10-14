"""Bind a newly produced skills note to its live envelope across late route changes.

Ownership stays in memory for this turn. Persisted markers cannot create it.

Until a request carrying the raw note has been built, a protected route projects the live message and
its durable row once (sticky). After that, bytes a provider may already have received are history: a
later protected route (fallback) projects only its own outgoing request copy.
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class _Delivery:
    message: dict
    note: str
    added: dict[str, str]
    removed: frozenset[str]
    key: int
    previous_told: Any
    part: dict | None = None
    content: list | None = None
    expected: str | None = None
    projected: bool = False
    raw_built: bool = False


def bind_skills_note(agent, message, note, *, part=None):
    pending = getattr(agent, "_skills_index_pending_delivery", None)
    agent._skills_index_pending_delivery = None
    if not pending or pending[0] != note:
        return
    agent._skills_index_delivery = _Delivery(
        message, *pending, part=part, content=message.get("content") if part else None,
        expected=part["text"] if part else None,
    )


def bind_skills_sidecar(agent, message, sidecar):
    delivery = getattr(agent, "_skills_index_delivery", None)
    if isinstance(delivery, _Delivery) and delivery.message is message and delivery.part is None:
        if isinstance(sidecar, str) and sidecar.endswith(delivery.note):
            delivery.expected = sidecar


def _project_note(agent, delivery):
    from agent.llm_egress_firewall import SanitizedTextRejected, validate_sanitized_text
    from agent.skills_index_delta import _OMITTED_KEY, _OMITTED_NOTICE, _project_entries, _render

    added = _project_entries(delivery.added, announce_omissions=True)
    withdrawn = _project_entries({name: "" for name in delivery.removed}, announce_omissions=True)
    if _OMITTED_KEY in withdrawn:
        added[_OMITTED_KEY] = _OMITTED_NOTICE
    removed = frozenset(name for name in withdrawn if name != _OMITTED_KEY)
    note = _render(added, set(removed))
    aggregate = int(getattr(agent, "_llm_egress_max_sanitized_bytes",
                            32_768 if getattr(agent, "_llm_egress_aux_task", "") == "vision" else 262_144))
    serialized = int(getattr(agent, "_llm_egress_max_serialized_bytes", 262_144))
    try:
        validate_sanitized_text(note, max_bytes=min(aggregate, serialized) // 4)
    except SanitizedTextRejected:
        return "", None
    return note, (added, removed)


def rebind_skills_note(agent, message):
    """Move ownership to compaction's copy of the current turn's row.

    Compaction rebuilds protected-tail rows with shallow copies, so identity alone would expire a live
    span. Only a copy that still holds the same ``content`` object and sidecar text inherits it; a
    rewritten or unrelated row stays unowned and meets the ordinary final gate.
    """
    delivery = getattr(agent, "_skills_index_delivery", None)
    if not isinstance(delivery, _Delivery) or delivery.message is message:
        return
    previous = delivery.message
    if (isinstance(message, dict) and message.get("role") == "user"
            and message.get("content") is previous.get("content")
            and message.get("api_content") == previous.get("api_content")):
        delivery.message = message


def _owned_text(message, delivery):
    """The live producer span's current text, or None once ownership has expired."""
    if delivery.part is None:
        current = message.get("api_content")
    else:
        content = message.get("content")
        if content is not delivery.content or sum(p is delivery.part for p in content) != 1:
            return None
        current = delivery.part.get("text")
    if not isinstance(current, str) or current != delivery.expected or not current.endswith(delivery.note):
        return None
    return current


def revalidate_skills_note(agent, message):
    """Project only the authenticated live producer span for the actual bound route.

    Before any request carrying the raw note was built, projection is sticky: the live message and its
    durable row are rewritten once, and a replaced/copied envelope expires ownership, leaving ordinary
    final-gate rejection intact. Durable write failure stops the request. Once a request carrying the
    raw note has been built, those bytes may already be with a provider and in its cached prefix, so a
    protected route gets ``(text, part_index)`` for its request copy only and nothing is rewritten.
    """
    from agent.chat_completion_helpers import _destination_requires_egress_firewall
    from agent.session_persistence import _durable_content, _persist_lock, durable_user_row_content
    from agent.skills_index_delta import _shown

    delivery = getattr(agent, "_skills_index_delivery", None)
    if not isinstance(delivery, _Delivery) or delivery.message is not message or delivery.projected:
        return None
    if not _destination_requires_egress_firewall(agent):
        delivery.raw_built = True
        return None
    if delivery.raw_built:
        current = _owned_text(message, delivery)
        if current is None:
            return None
        note, _ = _project_note(agent, delivery)
        projected = current[:-len(delivery.note)] + note
        if projected == current:
            return None
        index = None if delivery.part is None else next(
            i for i, p in enumerate(message["content"]) if p is delivery.part)
        return projected, index
    with _persist_lock(agent):
        current = _owned_text(message, delivery)
        if current is None:
            return None
        content = message.get("content")
        note, delta = _project_note(agent, delivery)
        projected = current[:-len(delivery.note)] + note
        db = getattr(agent, "_session_db", None)
        if projected != current:
            row_id = message.get("_row_id")
            if db is not None:
                if isinstance(row_id, bool) or not isinstance(row_id, int):
                    raise RuntimeError("Live skills projection has no durable row identity")
                if delivery.part is None:
                    durable, sidecar = durable_user_row_content(agent, message, message.get("content"), projected)
                    expected, old_sidecar = durable_user_row_content(agent, message, message.get("content"), current)
                    written = db.compare_and_set_user_message_projection(
                        agent.session_id, row_id, expected_content=expected, expected_api_content=old_sidecar,
                        content=durable, api_content=sidecar,
                    )
                else:
                    updated = [dict(p, text=projected) if p is delivery.part else p for p in content]
                    in_place = getattr(agent, "_last_compaction_in_place", False)
                    durable = updated if in_place else _durable_content(updated)
                    expected = content if in_place else _durable_content(content)
                    written = db.compare_and_set_user_message_projection(
                        agent.session_id, row_id, expected_content=expected, expected_api_content=message.get("api_content"),
                        content=durable, api_content=message.get("api_content"),
                    )
                if written != 1:
                    raise RuntimeError("Live skills projection durable row changed")
            if delivery.part is None:
                message["api_content"] = projected
            else:
                delivery.part["text"] = projected
        agent._skills_index_told = (
            (delivery.key, ({name: _shown(name, row) for name, row in delta[0].items()}, delta[1]))
            if delta is not None else delivery.previous_told
        )
        delivery.projected = True
