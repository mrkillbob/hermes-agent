"""Fork-only regression (see FORK_PATCHES.md): skill env passthrough is scoped per conversation.

A skill viewed in one conversation must not authorize its secret names for a sibling
conversation in the same process. Lives in its own file so an upstream sync cannot overwrite it.
"""

import pytest

import tools.env_passthrough as ep
from gateway.session_context import clear_session_vars, set_session_vars


@pytest.fixture(autouse=True)
def _clean():
    ep._allowed_env_vars.clear()
    yield
    ep._allowed_env_vars.clear()


def _in_session(session_id, fn):
    tokens = set_session_vars(session_id=session_id)
    try:
        return fn()
    finally:
        clear_session_vars(tokens)


def test_registration_is_invisible_to_sibling_session():
    _in_session("session-a", lambda: ep.register_env_passthrough(["SKILL_ONLY_TOKEN"]))

    assert _in_session("session-a", lambda: ep.is_env_passthrough("SKILL_ONLY_TOKEN"))
    assert not _in_session("session-b", lambda: ep.is_env_passthrough("SKILL_ONLY_TOKEN"))
    assert "SKILL_ONLY_TOKEN" not in _in_session("session-b", ep.get_all_passthrough)


def test_clear_only_resets_current_session():
    _in_session("session-a", lambda: ep.register_env_passthrough(["A_TOKEN"]))
    _in_session("session-b", lambda: ep.register_env_passthrough(["B_TOKEN"]))

    _in_session("session-a", ep.clear_env_passthrough)

    assert not _in_session("session-a", lambda: ep.is_env_passthrough("A_TOKEN"))
    assert _in_session("session-b", lambda: ep.is_env_passthrough("B_TOKEN"))
