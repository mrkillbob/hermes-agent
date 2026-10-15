from hermes_cli.kanban_db import Task
from hermes_cli.kanban_worker_routing import prepare_worker_route


def _task(provider):
    return Task(
        id="t_local", title="local card", body=None, assignee="a", status="running", priority=0,
        created_by="test", created_at=1, started_at=None, completed_at=None, workspace_kind="dir",
        workspace_path=None, claim_lock=None, claim_expires=None, tenant=None,
        model_override="qwen3", provider_override=provider)


def test_local_route_does_not_inherit_an_ambient_custom_endpoint(monkeypatch):
    monkeypatch.setattr("hermes_cli.kanban_worker_routing._resolve_process_local_provider_endpoint",
                        lambda provider: None)
    env = {"CUSTOM_BASE_URL": "https://remote.example/v1"}
    prepare_worker_route(_task("llamacpp"), None, env)
    assert env["HERMES_KANBAN_LOCAL_ONLY"] == "1"
    assert "CUSTOM_BASE_URL" not in env


def test_local_route_uses_the_resolved_loopback_endpoint(monkeypatch):
    monkeypatch.setattr("hermes_cli.kanban_worker_routing._resolve_process_local_provider_endpoint",
                        lambda provider: "http://127.0.0.1:11434/v1")
    env = {"CUSTOM_BASE_URL": "https://remote.example/v1"}
    prepare_worker_route(_task("llamacpp"), None, env)
    assert env["CUSTOM_BASE_URL"] == "http://127.0.0.1:11434/v1"
