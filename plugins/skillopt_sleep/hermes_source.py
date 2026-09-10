"""Read-only Hermes transcript adapter for SkillOpt-Sleep."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


_NEGATIVE = ("still broken", "doesn't work", "does not work", "not working", "wrong", "broken", "failing", "revert", "undo")
_POSITIVE = ("thanks", "thank you", "perfect", "great", "works now", "that works", "lgtm", "looks good", "fixed")


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(
            (part.get("text") or part.get("content")).strip()
            for part in value
            if isinstance(part, dict)
            and part.get("type", "text") in {"text", "input_text", "output_text"}
            and isinstance(part.get("text") or part.get("content"), str)
        ).strip()
    return ""


def _safe_text(value: Any, limit: int = 4000) -> str:
    text = _as_text(value)
    if not text:
        return ""
    try:
        from agent.redact import redact_sensitive_text
        text = redact_sensitive_text(text, force=True, redact_url_credentials=True)
    except Exception:
        # Never include the original exception: it may itself contain source text.
        raise RuntimeError("Hermes transcript redaction failed; harvesting stopped") from None
    return text[:limit]


def _iso(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return str(value)


def _epoch(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _project_matches(session: dict[str, Any], project: str) -> bool:
    if not project:
        return False
    requested = Path(project).expanduser().resolve()
    root = session.get("git_repo_root")
    raw = root or session.get("cwd")
    if not raw:
        return False
    try:
        current = Path(str(raw)).expanduser().resolve()
        return current == requested or (not root and requested in current.parents)
    except (OSError, RuntimeError, ValueError):
        return False


def _tool_name(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", value):
        return ""
    return value if _safe_text(value) == value else ""


def _feedback(prompts: Iterable[str]) -> list[str]:
    """Diagnostic lexical hints only; these are not verified outcome labels."""
    values: list[str] = []
    for prompt in prompts:
        low = prompt.lower()
        values.extend(f"neg:{phrase}" for phrase in _NEGATIVE if phrase in low)
        values.extend(f"pos:{phrase}" for phrase in _POSITIVE if phrase in low)
    return values


def harvest_hermes(*, project: str = "", limit: int = 40, lookback_hours: int = 72) -> list[Any]:
    """Return SkillOpt ``SessionDigest`` objects from the active Hermes profile."""
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB
    from skillopt_sleep.types import SessionDigest

    if not project:
        raise ValueError("Hermes harvesting requires an explicit project")
    project = str(Path(project).expanduser().resolve())
    cutoff = time.time() - max(0, int(lookback_hours or 0)) * 3600 if lookback_hours else 0
    db_path = get_hermes_home() / "state.db"
    if not db_path.exists():
        return []
    db = None
    try:
        db = SessionDB(db_path=db_path, read_only=True)
        digests: list[SessionDigest] = []
        for session in db.search_sessions(workspace_key=project, limit=max(1, int(limit or 1))):
            if not _project_matches(session, project):
                continue
            started = session.get("started_at") or ""
            activity = _epoch(session.get("last_active") or started)
            if cutoff and (not activity or activity < cutoff):
                continue
            messages = db.get_messages(session["id"], include_compacted=False)
            prompts: list[str] = []
            finals: list[str] = []
            tools: list[str] = []
            for message in messages:
                if message.get("_compressed_summary"):
                    continue
                role = str(message.get("role") or "")
                text = _safe_text(message.get("content")) if role in {"user", "assistant"} else ""
                if role == "user" and text:
                    prompts.append(text)
                elif role == "assistant" and text and not message.get("tool_calls"):
                    finals.append(text)
                tool_name = _tool_name(message.get("tool_name")) if role == "tool" else ""
                if tool_name and tool_name not in tools:
                    tools.append(tool_name)
                raw_calls = message.get("tool_calls") if role == "assistant" else None
                if isinstance(raw_calls, str):
                    try:
                        raw_calls = json.loads(raw_calls)
                    except json.JSONDecodeError:
                        raw_calls = []
                if isinstance(raw_calls, list):
                    for call in raw_calls:
                        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                            continue
                        name = _tool_name(call["function"].get("name"))
                        if name and name not in tools:
                            tools.append(name)
            if not prompts:
                continue
            digests.append(SessionDigest(
                session_id=str(session["id"]),
                project=project,
                git_branch=_safe_text(session.get("git_branch"), limit=256),
                started_at=_iso(started),
                ended_at=_iso(session.get("ended_at") or session.get("last_active") or ""),
                user_prompts=prompts,
                assistant_finals=finals[-8:],
                tools_used=tools,
                files_touched=[],
                feedback_signals=_feedback(prompts),
                n_user_turns=len(prompts),
                n_assistant_turns=len(finals),
                raw_path="",
            ))
        return digests
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"Hermes session store could not be read ({type(exc).__name__})") from None
    finally:
        if db is not None:
            db.close()
