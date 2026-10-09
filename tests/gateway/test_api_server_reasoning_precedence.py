"""Fork-only regression (see FORK_PATCHES.md): structured ``reasoning.effort`` beats the legacy
``reasoning_effort`` field when a client sends both."""

from gateway.platforms.api_server_request_options import _request_reasoning_config


def test_structured_effort_wins_over_legacy_field():
    opts = {"reasoning": {"enabled": True, "effort": "high"}, "reasoning_effort": "none"}
    assert _request_reasoning_config(opts) == {"enabled": True, "effort": "high"}


def test_legacy_field_used_when_structured_effort_absent():
    assert _request_reasoning_config({"reasoning_effort": "none"}) == {"enabled": False}
    assert _request_reasoning_config({"reasoning": {"enabled": True}, "reasoning_effort": "low"}) == {
        "enabled": True, "effort": "low"}
