"""A llama.cpp worker route must never reach, start, or be relabelled as Ollama.

Hermes itself never execs the ``ollama`` CLI (which on macOS auto-starts Ollama.app when the
server is down); these tests pin that dispatching a llama.cpp-pinned Kanban worker spawns only the
Hermes worker and carries only the llama.cpp endpoint, while an explicit Ollama pin still bridges
to its loopback endpoint.
"""

import json
import os
import subprocess

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_dispatch as kbd

_LLAMACPP_URL = "http://127.0.0.1:18434/v1"
_OLLAMA_URL = "http://127.0.0.1:11434/v1"
_LLAMACPP_ALIASES = ("llamacpp", "llama.cpp", "llama-cpp", "local")
_OLLAMA_STARTERS = ("ollama", "open", "brew", "launchctl")


def _task(provider: str, model: str) -> "kb.Task":
    return kb.Task(
        id="t_local_route",
        title="local route",
        body=None,
        assignee="researcher-a",
        status="running",
        priority=0,
        created_by="test",
        created_at=1,
        started_at=None,
        completed_at=None,
        workspace_kind="dir",
        workspace_path=None,
        claim_lock=None,
        claim_expires=None,
        tenant=None,
        model_override=model,
        provider_override=provider,
    )


@pytest.fixture
def dispatch(monkeypatch, tmp_path):
    """Run ``_default_spawn`` against a root config naming both local servers.

    Every process spawn and every outbound socket connection is recorded instead of executed.
    """
    root = tmp_path / ".hermes"
    profile = root / "profiles" / "researcher-a"
    profile.mkdir(parents=True)
    root.joinpath("config.yaml").write_text(
        "providers:\n"
        f"  ollama-launch:\n    api: {_OLLAMA_URL}\n"
        + "".join(f"  {alias}:\n    api: {_LLAMACPP_URL}\n" for alias in _LLAMACPP_ALIASES),
        encoding="utf-8",
    )
    profile.joinpath("config.yaml").write_text(
        "model:\n  provider: openai-codex\n  default: gpt-5.6-luna\n", encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(kbd, "_resolve_hermes_argv", lambda: ["hermes"])

    spawned: list[dict] = []
    connected: list[object] = []

    class FakeProc:
        pid = 4301

    def fake_popen(cmd, *args, **kwargs):
        spawned.append({"cmd": list(cmd), "env": dict(kwargs.get("env") or {})})
        return FakeProc()

    def fail_spawn(*args, **kwargs):
        raise AssertionError(f"unexpected process spawn: {args!r}")

    def record_connect(self, address):
        connected.append(address)
        raise OSError("network disabled in test")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(os, "system", fail_spawn)
    monkeypatch.setattr(os, "posix_spawn", fail_spawn, raising=False)
    monkeypatch.setattr("socket.socket.connect", record_connect)

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def run(provider: str, model: str) -> dict:
        assert kbd._default_spawn(_task(provider, model), str(workspace)) == FakeProc.pid
        (worker,) = spawned
        cmd = worker["cmd"]
        return {
            "cmd": cmd,
            "env": worker["env"],
            "route": json.loads(cmd[cmd.index("--route") + 1]),
            "connected": connected,
        }

    return run


@pytest.mark.parametrize("alias", _LLAMACPP_ALIASES)
def test_llamacpp_pin_spawns_only_the_worker_and_never_touches_ollama(dispatch, alias) -> None:
    result = dispatch(alias, "Qwen3.5-4B-Q6_K")

    assert "hermes_cli.kanban_worker_client" in result["cmd"]
    assert not [part for part in result["cmd"] if os.path.basename(part) in _OLLAMA_STARTERS]
    assert result["connected"] == []
    assert result["env"]["CUSTOM_BASE_URL"] == _LLAMACPP_URL
    assert result["route"]["base_url"] == _LLAMACPP_URL
    assert "11434" not in json.dumps(result["cmd"]) + json.dumps(result["env"])
    assert result["route"]["provider"] != "ollama"


def test_explicit_ollama_pin_still_bridges_to_its_loopback_endpoint(dispatch) -> None:
    result = dispatch("ollama-launch", "qwen3.5:4b")

    assert result["route"]["provider"] == "ollama"
    assert result["route"]["base_url"] == _OLLAMA_URL
    assert result["env"]["CUSTOM_BASE_URL"] == _OLLAMA_URL
    assert result["connected"] == []
