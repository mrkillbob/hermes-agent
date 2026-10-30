"""Projection CAS retains ordinary sidecar NULL representation."""
from hermes_state import SessionDB


def test_projection_cas_preserves_normalized_null_sidecar(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("test", source="cli")
        row = db.append_message("test", "user", "Run checks.")  # Ordinary writer normalized sidecar to NULL.
        assert db.compare_and_set_user_message_projection(
            "test", row, expected_content="Run checks.", expected_api_content="Run checks.",
            content="Run checks.", api_content="Run checks.\n\nSafe note.",
        ) == 1
        assert db.get_messages("test")[0]["api_content"] == "Run checks.\n\nSafe note."
    finally:
        db.close()
