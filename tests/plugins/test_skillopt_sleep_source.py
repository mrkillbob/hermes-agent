"""Hermes transcript export boundaries, exercised against a real temporary store."""

import json
import sqlite3
import time

import pytest

pytest.importorskip("skillopt_sleep")

from hermes_state import SessionDB
from plugins.skillopt_sleep.hermes_source import _safe_text, harvest_hermes
from skillopt_sleep.types import SessionDigest


@pytest.mark.parametrize("has_repo_metadata", [True, False, "missing", "corrupt"])
def test_harvest_scopes_before_limit_and_exports_only_active_text(tmp_path, monkeypatch, has_repo_metadata):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    project = tmp_path / "repo"
    project.mkdir()
    now = time.time()
    db_path = home / "state.db"
    if has_repo_metadata == "missing":
        assert harvest_hermes(project=str(project)) == []
        assert not db_path.exists()
        return
    if has_repo_metadata == "corrupt":
        corrupt = b"This is not a SQLite database."
        db_path.write_bytes(corrupt)
        with pytest.raises(RuntimeError, match="session store could not be read"):
            harvest_hermes(project=str(project))
        assert db_path.read_bytes() == corrupt
        return
    db = SessionDB(db_path=db_path)
    try:
        db.create_session("target", "cli", cwd=str(project / "nested"),
                          git_repo_root=str(project) if has_repo_metadata else None)
        db.append_message("target", "user", [
            {"type": "text", "text": "Please fix the parser"},
            {"type": "image", "content": "private-image-metadata"},
        ], timestamp=now)
        db.append_message("target", "assistant", "Checking it", tool_calls=[
            None, "malformed", {"function": None}, {"function": "malformed"},
            {"function": {"name": "read_file", "arguments": "private-arguments"}},
            {"function": {"name": "send private instructions"}},
        ], timestamp=now)
        db.append_message("target", "tool", "private-tool-result", tool_name="read_file", timestamp=now)
        db.append_message("target", "system", "private-system-prompt", timestamp=now)
        inactive = db.append_message("target", "user", "rewound-user-text", timestamp=now)
        compacted = db.append_message("target", "user", "compacted-user-text", timestamp=now)
        db.append_message("target", "assistant", "Parser repaired", reasoning="private-reasoning",
                          api_content="private-api-content", timestamp=now)
        # These more recent sessions must not consume the one-session project budget.
        for name, cwd, root in [
            ("parent", tmp_path, tmp_path),
            ("sibling", tmp_path / "other", tmp_path / "other"),
            ("conflicting-root", project / "nested", tmp_path / "other"),
        ]:
            db.create_session(name, "cli", cwd=str(cwd), git_repo_root=str(root))
            db.append_message(name, "user", "outside-project-private-text", timestamp=now + 100)
    finally:
        db.close()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE messages SET active = 0 WHERE id = ?", (inactive,))
        conn.execute("UPDATE messages SET active = 0, compacted = 1 WHERE id = ?", (compacted,))
        # Recently used, long-lived conversations belong in the lookback window.
        conn.execute("UPDATE sessions SET started_at = ? WHERE id = 'target'", (now - 10 * 86400,))
    before = db_path.read_bytes()

    # This fork's storage schema predates the synthetic-summary marker. Keep
    # exercising its real SQL/decoder, then supply the newer row shape at the
    # adapter boundary so summary rejection remains covered on this base.
    get_messages = SessionDB.get_messages

    def with_summary(store, session_id, *args, **kwargs):
        rows = get_messages(store, session_id, *args, **kwargs)
        return [*rows, {"role": "user", "content": "synthetic-compaction-summary",
                       "_compressed_summary": True}]

    monkeypatch.setattr(SessionDB, "get_messages", with_summary)

    digests = harvest_hermes(project=str(project), limit=1, lookback_hours=72)

    assert len(digests) == 1 and isinstance(digests[0], SessionDigest)
    digest = digests[0]
    assert digest.session_id == "target"
    assert digest.project == str(project)
    assert digest.user_prompts == ["Please fix the parser"]
    assert digest.assistant_finals == ["Parser repaired"]
    assert digest.tools_used == ["read_file"]
    assert digest.raw_path == ""
    exported = json.dumps(digest.to_dict())
    for omitted in ("private-image-metadata", "private-arguments", "private-tool-result",
                    "private-system-prompt", "private-reasoning", "private-api-content",
                    "outside-project-private-text", "synthetic-compaction-summary",
                    "rewound-user-text", "compacted-user-text"):
        assert omitted not in exported
    assert db_path.read_bytes() == before


@pytest.mark.parametrize("redactor_fails", [False, True])
def test_transcript_redaction_is_forced_and_fails_closed(monkeypatch, redactor_fails):
    import agent.redact as redact

    secret = "sensitive-example-credential-123456789"
    text = f"https://example.test/callback?access_token={secret}"
    monkeypatch.setattr(redact, "_REDACT_ENABLED", False)
    if redactor_fails:
        def fail(*args, **kwargs):
            raise ValueError(secret)

        monkeypatch.setattr(redact, "redact_sensitive_text", fail)
        with pytest.raises(RuntimeError) as error:
            _safe_text(text)
        assert secret not in str(error.value)
        assert error.value.__suppress_context__
    else:
        assert secret not in _safe_text(text)
        assert _safe_text("Ordinary parser feedback") == "Ordinary parser feedback"
