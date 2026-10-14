"""Addressed user-message projection writes; shared by turn assembly and routing."""
from typing import Any, Optional

from agent.message_sanitization import _sanitize_surrogates


class UserMessageProjectionMixin:
    def compare_and_set_user_message_projection(
        self, session_id: str, row_id: int, *, expected_content: Any,
        expected_api_content: Optional[str], content: Any, api_content: Optional[str],
    ) -> int:
        """Replace one live turn's projection only if both durable fields still match.

        Sidecars equal to content are stored as NULL by the ordinary write path.
        This is an addressed CAS, never a positional historical-context repair.
        """
        if not session_id or isinstance(row_id, bool) or not isinstance(row_id, int) or row_id <= 0:
            return 0
        expected_api_content = None if expected_api_content == expected_content else expected_api_content
        api_content = None if api_content == content else api_content
        return self._write_rowcount(
            "UPDATE messages SET content = ?, api_content = ? WHERE id = ? AND session_id = ? "
            "AND role = 'user' AND active = 1 AND content IS ? AND api_content IS ?",
            (self._encode_content(content), _sanitize_surrogates(api_content) if api_content is not None else None, row_id, session_id,
             self._encode_content(expected_content), _sanitize_surrogates(expected_api_content) if expected_api_content is not None else None))

    def set_user_message_content(self, session_id: str, row_id: int, content: Any) -> int:
        """Rewrite the content of ONE known active user row. Used when a user turn was written at submit
        time (before the agent ran) and the turn prologue then rewrote the prompt it persists (@-file
        expansion, native image parts): the early row must show what the transcript will replay, not the
        raw keystrokes, and the turn must not append a second row for the same input."""
        if not session_id or isinstance(row_id, bool) or not isinstance(row_id, int) or row_id <= 0:
            return 0
        return self._write_rowcount(
            "UPDATE messages SET content = ? WHERE id = ? AND session_id = ? AND role = 'user' AND active = 1",
            (self._encode_content(content), row_id, session_id))

