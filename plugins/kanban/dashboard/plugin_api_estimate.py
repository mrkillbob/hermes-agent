"""Rough token/complexity estimate for a kanban card via the auxiliary model."""
from __future__ import annotations

import json
import re
from typing import Optional

# Estimate: rough token/complexity read via the auxiliary model. NOT a dollar cost.
_ESTIMATE_SYSTEM_PROMPT = (
    "You estimate how much work an autonomous coding agent will spend on a "
    "kanban task. Given the task title and description, respond with STRICT "
    "JSON only (no prose, no code fence):\n"
    '{"est_tokens": <integer total tokens across the whole run>, '
    '"complexity": "S"|"M"|"L", '
    '"rationale": "<one short sentence>"}\n'
    "Base the token figure on a realistic multi-turn agent run (reading files, "
    "tool calls, edits, retries) — not a single reply. S≈small/localized, "
    "M≈multi-file, L≈broad or ambiguous. Be honest that this is a rough guess.")


def _cap(s: Optional[str], n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def run_estimate(title: str, body: Optional[str], *, task_id: Optional[str]) -> dict:
    """Never raises — config/parse/API errors become ``{"ok": False, "reason"}`` so the UI renders them inline."""
    if not (title or "").strip():
        return {"ok": False, "reason": "a title is required to estimate"}
    try:
        from agent.auxiliary_client import call_llm
    except Exception:
        return {"ok": False, "reason": "auxiliary client unavailable"}
    user_msg = f"Title: {_cap(title, 400)}\n\nDescription:\n{_cap(body, 4000) or '(none)'}"
    # Headless like specify/decompose's _call_aux: without a bound affinity scope the relay-affinity
    # headers are omitted and the OpenCode Go relay answers 400 MissingSessionID (#112043). The
    # create dialog has no task yet, so it shares one stable key.
    from agent.portal_tags import get_affinity_scope, reset_affinity_scope, set_affinity_scope
    affinity_token = None if get_affinity_scope() else set_affinity_scope(f"kanban:{task_id or 'estimate'}")
    try:
        resp = call_llm(
            task="kanban_estimator",
            messages=[{"role": "system", "content": _ESTIMATE_SYSTEM_PROMPT}, {"role": "user", "content": user_msg}],
            temperature=0.0, max_tokens=300, timeout=60)
    except Exception as exc:
        return {"ok": False, "reason": f"LLM error: {type(exc).__name__}"}
    finally:
        if affinity_token is not None:
            reset_affinity_scope(affinity_token)
    try:
        raw = (resp.choices[0].message.content or "").strip()
        model = getattr(resp, "model", None)
    except Exception:
        raw, model = "", None

    # Same tolerant JSON-blob extraction the specifier uses.
    try:
        m = None if raw.lstrip().startswith("{") else re.search(r"\{.*\}", raw, re.DOTALL)
        obj = json.loads(m.group(0) if m else raw)
        parsed = obj if isinstance(obj, dict) else None
    except Exception:
        parsed = None
    if not parsed:
        return {"ok": False, "reason": "could not parse an estimate from the model"}
    try:
        est_tokens = int(parsed.get("est_tokens") or 0)
    except (TypeError, ValueError):
        est_tokens = 0
    complexity = str(parsed.get("complexity") or "").strip().upper()
    return {
        "ok": True, "est_tokens": est_tokens, "complexity": complexity if complexity in {"S", "M", "L"} else None,
        "rationale": str(parsed.get("rationale") or "").strip() or None, "model": model}
