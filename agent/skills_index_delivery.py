"""Bind a newly produced skills note to its live envelope across late route changes.

Ownership stays in memory for this turn. Persisted markers cannot create it.

Until a request carrying the raw note has been built, a protected route projects the live message and
its durable row once (sticky). After that, bytes a provider may already have received are history: the
raw note is remembered in memory, and a protected route (a fallback, this turn or a later one) projects
only its own outgoing request copy, wherever the note sits in it.
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


def _project_note(agent, raw_added, raw_removed):
    from agent.llm_egress_firewall import SanitizedTextRejected, validate_sanitized_text
    from agent.skills_index_delta import _OMITTED_KEY, _OMITTED_NOTICE, _project_entries, _render

    added = _project_entries(raw_added, announce_omissions=True)
    withdrawn = _project_entries({name: "" for name in raw_removed}, announce_omissions=True)
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


def _remember_sent(agent, delivery):
    """Process-local record that a request carrying this raw note was built for an unprotected route.

    Only the producer writes it, and only after such a build, so text that was never sent as a note
    cannot be repaired by it. Keyed on the whole text that was sent, so a user who quotes the note
    in some other message is not mistaken for the row that carried it. An entry lives while its row
    is in the transcript (see :func:`project_sent_skills_notes`), never by count.
    """
    sent = getattr(agent, "_skills_index_sent_notes", None)
    if not isinstance(sent, dict):
        sent = agent._skills_index_sent_notes = {}
    sent.pop(delivery.expected, None)  # A, B, A again: the latest occurrence is the newest.
    sent[delivery.expected] = (delivery.note, dict(delivery.added), delivery.removed, delivery.message.get("content"))


def _user_texts(message):
    content = message.get("content") if message.get("role") == "user" else None
    if isinstance(content, str):
        return (content,)
    if isinstance(content, list):
        return tuple(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ()


def _rewrite_user_texts(message, rewrite):
    """``message`` with ``rewrite(text, ordinal)`` applied to each user text; the same dict when unchanged."""
    texts = _user_texts(message)
    new = [rewrite(text, ordinal) for ordinal, text in enumerate(texts)]
    if new == list(texts):
        return message
    if isinstance(message["content"], str):
        return {**message, "content": new[0]}
    ordinal = iter(new)
    return {**message, "content": [
        {**p, "text": next(ordinal)} if isinstance(p, dict) and isinstance(p.get("text"), str) else p
        for p in message["content"]]}


def _carrier(sent, text):
    """The remembered sent text ``text`` ends with; the longest wins when one carrier ends another."""
    return max((key for key in sent if text.endswith(key)), key=len, default=None)


def restore_sent_sidecars(agent, messages):
    """Put a remembered string row's ``api_content`` back into ``content``.

    A string turn carries its note only in the sidecar, and a context engine's selection hands back
    clones whose sidecar the persistence stripping then drops; ``build_api_messages`` substitutes it for
    the same reason. Only a sidecar that is exactly a remembered sent text is restored, and only while
    the row still holds the content it had when that text was sent: a context engine that replaced the
    text (a summary, say) keeps its selection instead of getting the old sidecar back.
    """
    sent = getattr(agent, "_skills_index_sent_notes", None)
    if not sent:
        return messages

    def unedited(m):
        entry = sent.get(m.get("api_content"))
        return entry is not None and m.get("content") == entry[3]

    restored = [{**m, "content": m["api_content"]}
                if m.get("role") == "user" and isinstance(m.get("content"), str) and unedited(m) else m
                for m in messages]
    return messages if all(a is b for a, b in zip(restored, messages)) else restored


def project_sent_skills_notes(agent, api_messages, *, prune=False):
    """Return ``api_messages`` with every remembered raw note projected for a protected route.

    Request-copy only: matching user rows (or text parts) are replaced by new dicts, never mutated, so
    the live transcript, the durable rows and the primary route's cached bytes stay as they were sent.
    Only the newest remembered note is projected, and only at its last occurrence in the request; every
    superseded note, and any earlier row carrying the same text, is omitted. That keeps the notes of a
    long session inside a single note's share of the request budget. Keyed on the sent text rather than
    row identity because a context engine's selection can hand back clones that carry none, and because a
    note sent on an earlier turn is history by now.

    ``prune`` is for the full canonical request only: a note whose row compaction has removed is
    forgotten, while one still in the transcript is kept however many notes were sent. A context
    engine's selection may drop rows that are still history, so it never prunes.
    """
    sent = getattr(agent, "_skills_index_sent_notes", None)
    if not sent:
        return api_messages
    if prune:
        texts = [t for m in api_messages for t in _user_texts(m)]
        for key in [key for key in sent if not any(t.endswith(key) for t in texts)]:
            del sent[key]
        if not sent:
            return api_messages
    from agent.chat_completion_helpers import _destination_requires_egress_firewall

    if not _destination_requires_egress_firewall(agent):
        return api_messages
    newest = next(reversed(sent))
    projected = _project_note(agent, *sent[newest][1:3])[0]
    last = max(((i, n) for i, m in enumerate(api_messages) for n, t in enumerate(_user_texts(m))
                if _carrier(sent, t) == newest), default=None)

    def rewrite_at(index):
        def rewrite(text, ordinal):
            key = _carrier(sent, text)
            if key is None:
                return text
            keep = projected if key == newest and (index, ordinal) == last else ""
            return text[:-len(sent[key][0])] + keep
        return rewrite

    return [_rewrite_user_texts(m, rewrite_at(i)) for i, m in enumerate(api_messages)]


def revalidate_skills_note(agent, message):
    """Project only the authenticated live producer span for the actual bound route.

    Before any request carrying the raw note was built, projection is sticky: the live message and its
    durable row are rewritten once, and a replaced/copied envelope expires ownership, leaving ordinary
    final-gate rejection intact. Durable write failure stops the request. Once a request carrying the
    raw note has been built, those bytes may already be with a provider and in its cached prefix: the
    note is remembered and nothing is rewritten; :func:`project_sent_skills_notes` handles protected
    routes on the request copy.
    """
    from agent.chat_completion_helpers import _destination_requires_egress_firewall
    from agent.session_persistence import _durable_content, _persist_lock, durable_user_row_content
    from agent.skills_index_delta import _shown

    delivery = getattr(agent, "_skills_index_delivery", None)
    if not isinstance(delivery, _Delivery) or delivery.message is not message or delivery.projected:
        return
    if not _destination_requires_egress_firewall(agent):
        if _owned_text(message, delivery) is not None:
            delivery.raw_built = True
            _remember_sent(agent, delivery)
        return
    if delivery.raw_built:
        return
    with _persist_lock(agent):
        current = _owned_text(message, delivery)
        if current is None:
            return
        content = message.get("content")
        note, delta = _project_note(agent, delivery.added, delivery.removed)
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
