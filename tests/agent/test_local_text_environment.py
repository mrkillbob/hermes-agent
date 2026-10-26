"""Neither eager nor lazy prompt construction may start a toolchain probe."""
from types import SimpleNamespace


def test_constructor_cannot_warm_environment_probe(monkeypatch):
    from agent.agent_init import _apply_agent_section
    from agent.local_text_policy import local_text_scope
    from tools import env_probe
    calls = []
    monkeypatch.setattr(env_probe,'warm_environment_probe_async',lambda:calls.append('probe'))
    agent = SimpleNamespace(run_budget_seconds=None)
    with local_text_scope(True):
        _apply_agent_section(agent, {})
    assert agent._environment_probe is False and calls == []


def test_lazy_prompt_does_not_restore_stale_probe(monkeypatch):
    import pytest
    from agent.system_prompt import _post_workspace_parts
    from agent.local_text_policy import local_text_scope
    from tools import env_probe
    def forbidden(): pytest.fail('lazy environment probe must not run')
    monkeypatch.setattr(env_probe,'get_environment_probe_line',forbidden)
    with local_text_scope(True):
        assert _post_workspace_parts(SimpleNamespace(_environment_probe=True)) == []
