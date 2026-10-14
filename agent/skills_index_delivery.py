"""Bind a newly produced skills note to its live envelope across late route changes.

Ownership of the live envelope stays in memory for this turn. Persisted markers cannot create it.

Until a request carrying the raw note has been built, a protected route projects the live message and
its durable row once (sticky), and an unprotected route restores a note that was staged projected.
After that, bytes a provider may already have received are history: a protected route (a fallback, this
turn or a later one) projects only its own outgoing request copy.

That projection is structural, not remembered. A note an agent attached has one shape (a canonical
note that ends the replayed sidecar of a string turn, or the last text part of a multimodal turn, set
off by a blank line), so any request copy, from any agent over any transcript, projects the same bytes
for the same note: nothing is lost when the agent is rebuilt, and text a user typed or pasted into their
own message is never read as a note.
"""
from dataclasses import dataclass
from typing import Any

from agent.message_metadata import SKILLS_NOTE_CARRIER


@dataclass
class _Delivery:
    message: dict
    note: str
    added: dict[str, str]
    removed: frozenset[str]
    key: int
    previous_told: Any
    raw_added: dict[str, str]
    raw_removed: frozenset[str]
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
    if part is not None:
        message[SKILLS_NOTE_CARRIER] = True  # The part list carries no other proof the note is the agent's.
    agent._skills_index_delivery = _Delivery(
        message, *pending, part=part, content=message.get("content") if part else None,
        expected=part["text"] if part else None,
    )


def bind_skills_sidecar(agent, message, sidecar):
    delivery = getattr(agent, "_skills_index_delivery", None)
    if isinstance(delivery, _Delivery) and delivery.message is message and delivery.part is None:
        if isinstance(sidecar, str) and sidecar.endswith(delivery.note):
            delivery.expected = sidecar


def _note_budget(agent):
    """One quarter of the smaller request cap: what skills notes may spend of a protected request."""
    aggregate = int(getattr(agent, "_llm_egress_max_sanitized_bytes",
                            32_768 if getattr(agent, "_llm_egress_aux_task", "") == "vision" else 262_144))
    serialized = int(getattr(agent, "_llm_egress_max_serialized_bytes", 262_144))
    return min(aggregate, serialized) // 4


def _project_note(agent, raw_added, raw_removed):
    from agent.llm_egress_firewall import SanitizedTextRejected, validate_sanitized_text
    from agent.skills_index_delta import _OMITTED_KEY, _OMITTED_NOTICE, _project_entries, _render

    added = _project_entries(raw_added, announce_omissions=True)
    withdrawn = _project_entries({name: "" for name in raw_removed}, announce_omissions=True)
    if _OMITTED_KEY in withdrawn:
        added[_OMITTED_KEY] = _OMITTED_NOTICE
    removed = frozenset(name for name in withdrawn if name != _OMITTED_KEY)
    note = _render(added, set(removed))
    try:
        validate_sanitized_text(note, max_bytes=_note_budget(agent))
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


def _note_start(text):
    """Where the canonical skills note that ends ``text`` begins, else None.

    An agent attaches a note after a blank line (or alone); anything else that merely ends in the same
    bytes, such as a user quoting a note mid-sentence, is not one. Only a note that survives a parse and
    re-render byte for byte counts, so a malformed or truncated note is left alone.
    """
    from agent.skills_index_delta import _NOTE_PREFIX, _parse, _render

    start = text.rfind(_NOTE_PREFIX)
    if start < 0 or (start and not text[:start].endswith("\n\n")):
        return None
    note = text[start:]
    return start if _render(*_parse(note[len(_NOTE_PREFIX):])) == note else None


def _parse_note(note):
    from agent.skills_index_delta import _NOTE_PREFIX, _parse

    return _parse(note[len(_NOTE_PREFIX):])


def _text_of(message, part):
    content = message["content"]
    return content if part is None else content[part]["text"]


def _note_slots(api_messages, sidecar_rows):
    """``(message index, text-part index or None, note start)`` for every note an agent attached.

    ``sidecar_rows`` are the rows an agent composed: a string turn's replayed sidecar, or a multimodal
    turn it appended a note part to (``SKILLS_NOTE_CARRIER``). The user's own text is never searched.
    A multimodal turn carries the note as one of its last two text parts (the note part, then the
    turn's context part).
    """
    slots = []
    for i, m in enumerate(api_messages):
        if m.get("role") != "user" or i not in sidecar_rows:
            continue
        content = m.get("content")
        if isinstance(content, str):
            if (start := _note_start(content)) is not None:
                slots.append((i, None, start))
        elif isinstance(content, list):
            texts = [n for n, p in enumerate(content) if isinstance(p, dict) and isinstance(p.get("text"), str)]
            # The note part is followed by at most the turn's one context part, and that part's own text
            # may end in a note's shape, so every candidate is a slot rather than only the last.
            for n in texts[-2:]:
                if (start := _note_start(content[n]["text"])) is not None:
                    slots.append((i, n, start))
    return slots


def remember_newest_skills_note(agent, api_messages, sidecar_rows):
    """Record the raw difference the newest note in the canonical request carries.

    A context engine's selection can drop that note's row; :func:`keep_newest_skills_note` then
    restores its difference. It is derived from the same transcript that produced the request, so it
    adds no trust beyond the note itself.
    """
    slots = _note_slots(api_messages, sidecar_rows)
    if not slots:
        agent._skills_index_newest_carrier = None
        return
    last = slots[-1][0]
    i, part, start = next(slot for slot in slots if slot[0] == last)  # The note part precedes the context part.
    agent._skills_index_newest_carrier = _parse_note(_text_of(api_messages[i], part)[start:])


def _same_text_ignoring_layout(content, sidecar, start):
    """Whether a stored flat ``content`` is the row whose sidecar ends in the note at ``start``.

    The store flattens a part list with single newlines while its sidecar joins the parts with blank lines
    and moves the note last, so the two only agree once the note and the whitespace between parts are set aside.
    """
    note = sidecar[start:]
    return " ".join(content.replace(note, "", 1).split()) == " ".join(sidecar[:start].split())


def restore_sidecar_content(messages):
    """Put a string row's replayed sidecar back into ``content`` when it carries a skills note.

    A context engine's selection hands back clones whose sidecar the persistence stripping then
    drops, and ``build_api_messages`` substitutes it for the same reason. Only a sidecar that extends
    the row's current content (or restates a stored multimodal row) is restored: a context engine that
    replaced the text (a summary, say) keeps its selection. Returns the messages and the indices that
    were restored, plus the multimodal rows an agent appended a note part to.
    """
    out, restored = [], set()
    for i, m in enumerate(messages):
        content, sidecar = m.get("content"), m.get("api_content")
        if m.get("role") == "user" and isinstance(content, list) and m.get(SKILLS_NOTE_CARRIER):
            restored.add(i)
        elif (m.get("role") == "user" and isinstance(content, str) and isinstance(sidecar, str)
                and (start := _note_start(sidecar)) is not None
                and (sidecar.startswith(content + "\n\n") or _same_text_ignoring_layout(content, sidecar, start))):
            out.append({**m, "content": sidecar})
            restored.add(i)
            continue
        out.append(m)
    return (out, frozenset(restored)) if restored else (messages, frozenset())


def project_skills_notes(agent, api_messages, sidecar_rows=frozenset()):
    """Return ``api_messages`` with every skills note projected for a protected route.

    Request-copy only: rows are replaced by new dicts, never mutated, so the live transcript, the durable
    rows and the primary route's cached bytes stay as they were sent. Each note is projected in place
    and deterministically, so a route that is activated again later receives the same bytes for every
    earlier note and only the new note is appended. Notes together may spend one note's share of the
    request budget. They fill it oldest first: a note is withheld when it does not fit after the ones
    before it, so a new note never changes what an earlier row already received.
    """
    from agent.chat_completion_helpers import _destination_requires_egress_firewall

    if not _destination_requires_egress_firewall(agent):
        return api_messages
    slots = _note_slots(api_messages, sidecar_rows)
    if not slots:
        return api_messages
    notes = [_project_note(agent, *_parse_note(_text_of(api_messages[i], part)[start:]))[0]
             for i, part, start in slots]
    spent, budget = 0, _note_budget(agent)
    for k, note in enumerate(notes):
        size = len(note.encode("utf-8"))
        if spent + size > budget:
            notes[k] = ""
        else:
            spent += size
    out = list(api_messages)
    edited: dict[int, list] = {}  # message index -> its parts, with a dropped part held as None until the end
    for (i, part, start), note in zip(slots, notes, strict=True):
        text = _text_of(api_messages[i], part)
        new = text[:start] + note if note else text[:start].removesuffix("\n\n")
        if new == text:
            continue
        if part is None:
            out[i] = {**api_messages[i], "content": new}
            continue
        parts = edited.setdefault(i, list(api_messages[i]["content"]))
        parts[part] = {**parts[part], "text": new} if new else None
    for i, parts in edited.items():
        out[i] = {**api_messages[i], "content": [p for p in parts if p is not None]}
    return api_messages if all(a is b for a, b in zip(out, api_messages, strict=True)) else out


def keep_newest_skills_note(agent, api_messages):
    """Make sure a protected selection still carries the newest difference.

    A context engine may keep an older note's row and drop the newest one's; the newest note says
    what the system prompt's index no longer shows, so it rides the last user row of the selection.
    """
    from agent.chat_completion_helpers import _destination_requires_egress_firewall

    newest = getattr(agent, "_skills_index_newest_carrier", None)
    if not newest or not _destination_requires_egress_firewall(agent):
        return api_messages
    note = _project_note(agent, *newest)[0]
    every_row = frozenset(range(len(api_messages)))
    if not note or any(_text_of(api_messages[i], part)[start:] == note
                       for i, part, start in _note_slots(api_messages, every_row)):
        return api_messages
    last = next((i for i in range(len(api_messages) - 1, -1, -1) if api_messages[i].get("role") == "user"), None)
    if last is None:
        return api_messages
    content = api_messages[last].get("content")
    new = (content + "\n\n" + note if isinstance(content, str) else
           [*content, {"type": "text", "text": note}] if isinstance(content, list) else None)
    if new is None:
        return api_messages
    return [{**m, "content": new} if i == last else m for i, m in enumerate(api_messages)]


def _rewrite_owned(agent, delivery, message, current, new_note):
    """Replace the owned span's note with ``new_note`` on the live message and, by CAS, its durable row."""
    from agent.session_persistence import _durable_content, _multimodal_note_sidecar, durable_user_row_content

    content = message.get("content")
    rewritten = current[:-len(delivery.note)] + new_note
    if rewritten == current:
        return
    db = getattr(agent, "_session_db", None)
    row_id = message.get("_row_id")
    if db is not None:
        if isinstance(row_id, bool) or not isinstance(row_id, int):
            raise RuntimeError("Live skills projection has no durable row identity")
        if delivery.part is None:
            durable, sidecar = durable_user_row_content(agent, message, message.get("content"), rewritten)
            expected, old_sidecar = durable_user_row_content(agent, message, message.get("content"), current)
            written = db.compare_and_set_user_message_projection(
                agent.session_id, row_id, expected_content=expected, expected_api_content=old_sidecar,
                content=durable, api_content=sidecar,
            )
        else:
            updated = [dict(p, text=rewritten) if p is delivery.part else p for p in content]
            in_place = getattr(agent, "_last_compaction_in_place", False)
            durable = updated if in_place else _durable_content(updated)
            expected = content if in_place else _durable_content(content)
            # The flush stores a sidecar for a list that carries a note; the live message has none.
            sidecar = message.get("api_content")
            written = db.compare_and_set_user_message_projection(
                agent.session_id, row_id, expected_content=expected,
                expected_api_content=sidecar if sidecar is not None else _multimodal_note_sidecar(content),
                content=durable, api_content=sidecar if sidecar is not None else _multimodal_note_sidecar(updated),
            )
        if written != 1:
            raise RuntimeError("Live skills projection durable row changed")
    if delivery.part is None:
        message["api_content"] = rewritten
    else:
        delivery.part["text"] = rewritten


def revalidate_skills_note(agent, message):
    """Bind the owned live producer span to the actual route.

    Before any request carrying the raw note was built, the rewrite is sticky: on a protected route the
    live message and its durable row are projected once, and on an unprotected route a note that was
    staged projected (the session started on a protected route) is restored to the full difference. A
    replaced/copied envelope expires ownership, leaving ordinary final-gate rejection intact. Durable
    write failure stops the request. Once a request carrying the raw note has been built, those bytes may
    already be with a provider and in its cached prefix, so nothing is rewritten;
    :func:`project_skills_notes` handles protected routes on the request copy.
    """
    from agent.chat_completion_helpers import _destination_requires_egress_firewall
    from agent.session_persistence import _persist_lock
    from agent.skills_index_delta import _render, _shown

    delivery = getattr(agent, "_skills_index_delivery", None)
    if not isinstance(delivery, _Delivery) or delivery.message is not message or delivery.projected:
        return
    if not _destination_requires_egress_firewall(agent):
        if not delivery.raw_built:
            raw_note = _render(delivery.raw_added, set(delivery.raw_removed))
            with _persist_lock(agent):
                current = _owned_text(message, delivery)
                if current is None:
                    return
                if raw_note != delivery.note:
                    _rewrite_owned(agent, delivery, message, current, raw_note)
                    delivery.expected = message.get("api_content") if delivery.part is None else delivery.part["text"]
                    delivery.note = raw_note
                    agent._skills_index_told = (delivery.key, (
                        {name: _shown(name, row) for name, row in delivery.raw_added.items()}, delivery.raw_removed))
        if _owned_text(message, delivery) is not None:
            delivery.raw_built = True
        return
    if delivery.raw_built:
        return
    with _persist_lock(agent):
        current = _owned_text(message, delivery)
        if current is None:
            return
        note, delta = _project_note(agent, delivery.raw_added, delivery.raw_removed)
        _rewrite_owned(agent, delivery, message, current, note)
        agent._skills_index_told = (
            (delivery.key, ({name: _shown(name, row) for name, row in delta[0].items()}, delta[1]))
            if delta is not None else delivery.previous_told
        )
        delivery.projected = True
