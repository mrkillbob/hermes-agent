"""Remote assignments select public task context without changing local context."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from agent.delegation_context import non_dispatcher_owned_context
from agent.system_prompt import build_system_prompt_parts
from tests.agent.test_system_prompt import _make_agent


@pytest.mark.parametrize("endpoint,owner,private_expected", [
    ("https://integrate.api.nvidia.com/v1", True, False),
    ("http://127.0.0.1:11434/v1", True, True),
    ("https://integrate.api.nvidia.com/v1", False, True),
])
def test_public_remote_assignment_does_not_load_ambient_context(
    tmp_path, monkeypatch, endpoint, owner, private_expected,
):
    home = tmp_path / "profile"
    home.mkdir()
    (home / "SOUL.md").write_text("PRIVATE_ROLE_HISTORY")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("PRIVATE_PROJECT_CONTEXT")
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TERMINAL_CWD", str(workspace))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_public")
    monkeypatch.delenv("HERMES_KANBAN_PROTECTED_REMOTE", raising=False)
    memory = SimpleNamespace(format_for_system_prompt=lambda _: "PRIVATE_MEMORY")
    agent = _make_agent(
        provider="nvidia", base_url=endpoint, api_mode="chat_completions",
        platform="cli", _memory_store=memory, _memory_enabled=True,
        _user_profile_enabled=True, _session_db=SimpleNamespace(db_path=home / "state.db"),
    )
    with nullcontext() if owner else non_dispatcher_owned_context():
        parts = build_system_prompt_parts(agent, "Approved public assignment.")
    text = "\n".join(parts.values())
    assert "Approved public assignment." in text
    for private in ("PRIVATE_ROLE_HISTORY", "PRIVATE_PROJECT_CONTEXT", "PRIVATE_MEMORY"):
        assert (private in text) is private_expected
    assert (str(workspace) in text) is private_expected


def test_remote_task_does_not_enumerate_local_skills_or_autoload_history(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_public")
    agent = _make_agent(
        provider="nvidia", base_url="https://integrate.api.nvidia.com/v1",
        api_mode="chat_completions", valid_tool_names={"skill_view", "read_file"},
        skip_context_files=True, platform="cli",
    )
    from agent import system_prompt

    def forbidden(*args, **kwargs):
        raise AssertionError("ambient inventory must not be loaded for a public task")

    monkeypatch.setattr(system_prompt, "_skills_prompt", forbidden)
    monkeypatch.setattr(system_prompt, "_auto_load_skills_prompt", forbidden)
    parts = build_system_prompt_parts(agent, "Approved public assignment.")
    assert "Approved public assignment." in parts["context"]
    assert "relative paths" in "\n".join(parts.values())
    assert agent.valid_tool_names == {"skill_view", "read_file"}


def test_context_diagnostics_cannot_certify_cached_private_prompt(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_public")
    agent = _make_agent(
        provider="nvidia", base_url="https://integrate.api.nvidia.com/v1",
        api_mode="chat_completions", skip_context_files=True,
        _cached_system_prompt="Private local history.", _remote_task_context_mode="local",
    )
    build_system_prompt_parts(agent)
    assert agent._remote_task_context_mode == "local"
    assert agent._cached_system_prompt == "Private local history."


def test_public_context_provenance_is_bound_to_rendered_bytes(tmp_path, monkeypatch):
    from agent.llm_egress_firewall import EgressBlocked
    from agent.remote_task_context import require_public_task_context
    from agent.system_prompt import build_system_prompt

    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_public")
    agent = _make_agent(
        provider="nvidia", base_url="https://integrate.api.nvidia.com/v1",
        api_mode="chat_completions", skip_context_files=True,
        _llm_egress_state_dir=tmp_path / "egress",
    )
    public = build_system_prompt(agent)
    agent._cached_system_prompt = public
    require_public_task_context(agent, route=agent)
    agent._cached_system_prompt = "Private local history."
    with pytest.raises(EgressBlocked):
        require_public_task_context(agent, route=agent)
