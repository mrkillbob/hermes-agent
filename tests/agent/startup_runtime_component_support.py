"""Private local component harness for independently reviewed runtime-state contracts.

This models the routed-kwargs -> assignment -> snapshot sequence. It does not
construct AIAgent, exercise restore, qualify a full startup, or establish a RED.
Only root may authorize execution after reviewing import effects and preflight.
"""

import hashlib
import importlib
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[2]
PRIMARY_URL = "https://primary.invalid/v1"
FALLBACK_URL = "https://fallback.invalid/v1"


class EffectDenied(BaseException):
    """Do not let production best-effort Exception handlers hide forbidden I/O."""


def _deny(*args, **kwargs):
    raise EffectDenied("component candidate forbids networking and process launch")


def _absent(path):
    # Metadata only; dangling symlinks and inaccessible paths must also refuse.
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise AssertionError(f"pre-import metadata gate refuses existing path: {path}")


@pytest.fixture(scope="module")
def real_components(tmp_path_factory, request):
    tmp_path = tmp_path_factory.mktemp("preferred-component")
    monkeypatch = pytest.MonkeyPatch()
    request.addfinalizer(monkeypatch.undo)
    # Import inside the test call, after normal pytest call-phase state exists.
    assert "PYTEST_CURRENT_TEST" in os.environ
    assert SOURCE.is_dir() and SOURCE.resolve() == SOURCE
    assert not any(name == "agent" or name.startswith("agent.")
                   or name == "hermes_bootstrap" or name == "run_agent"
                   for name in sys.modules), "requires a clean file-isolated process"
    assert not any(name.startswith("_hermes_") for name in vars(sys)), (
        "inherited Hermes launcher state is outside this candidate's scope"
    )
    # Install fences before ANY tested production imports. These are accident
    # prevention, not a security boundary or substitute for root's review.
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr",
                 "create_connection"):
        monkeypatch.setattr(socket, name, _deny)
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, _deny)
    class DeniedPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            _deny(*args, **kwargs)
    # Preserve Popen's class contract so the canonical live-system guard can
    # wrap/subclass it; no parent constructor or process creation is invoked.
    monkeypatch.setattr(subprocess, "Popen", DeniedPopen)
    for name in ("system", "popen", "execv", "execve", "execvp", "execvpe",
                 "posix_spawn", "posix_spawnp", "fork"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _deny)

    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    hermes_home = isolated_home / ".hermes"
    hermes_home.mkdir()
    # Remove inherited Hermes routing/location/policy selectors and provider
    # secrets without printing their names or values. Synthetic resolver below
    # never consults credentials. No genuine production guard is monkeypatched.
    for name in tuple(os.environ):
        if (name.startswith("HERMES_") or name.endswith(("_API_KEY", "_TOKEN"))
                or name in {"OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "XDG_STATE_HOME",
                            "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "LOCALAPPDATA", "APPDATA"}):
            monkeypatch.delenv(name)
    for name, value in {
        "HOME": isolated_home, "USERPROFILE": isolated_home,
        "HERMES_HOME": hermes_home, "HERMES_TEST_ISOLATION": hermes_home,
        "HERMES_DISABLE_LAZY_INSTALLS": "1", "HERMES_TEST_SANDBOX_HOME": hermes_home,
        "TMPDIR": tmp_path, "TMP": tmp_path, "TEMP": tmp_path,
        "LOCALAPPDATA": isolated_home / "AppData" / "Local",
        "APPDATA": isolated_home / "AppData" / "Roaming",
    }.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setattr(Path, "home", lambda: isolated_home)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)

    # No source reads or extracted functions. This gate checks only narrowly
    # identified metadata that could route real bootstrap toward managed state.
    for root in (SOURCE, SOURCE.parent):
        _absent(root / "install-stamp.json")
    assert not (SOURCE / ".git").is_symlink()
    assert not (SOURCE / ".git").is_file(), "linked worktree metadata needs separate review"
    for path in (
        SOURCE / ".hermes-update-zip-swap", SOURCE / ".git" / "hermes-update-pull",
        SOURCE / ".update-incomplete", SOURCE / ".lazy-refresh-incomplete",
        SOURCE / "manifest.json", SOURCE.parent / "manifest.json",
    ):
        _absent(path)
    install_key = hashlib.sha256(str(SOURCE).encode()).hexdigest()[:16]
    _absent(hermes_home / "installs" / install_key)
    _absent(SOURCE.parent / "installs" / install_key)
    bytecode_lookup = tmp_path / 'fresh-bytecode-lookup'
    bytecode_lookup.mkdir()
    assert not list(bytecode_lookup.iterdir())
    monkeypatch.setattr(sys, 'pycache_prefix', str(bytecode_lookup))
    monkeypatch.syspath_prepend(str(SOURCE))
    from hermes_constants import get_hermes_home, get_default_hermes_root
    assert get_hermes_home().resolve() == hermes_home.resolve()
    assert get_default_hermes_root().resolve() == hermes_home.resolve()
    from pm.environments import install_state_dir
    assert install_state_dir(SOURCE).resolve() == hermes_home.resolve() / 'installs' / install_key
    _absent(install_state_dir(SOURCE))
    for name in ('.startup-hold.json', '.drain_request.json'):
        _absent(hermes_home / name)

    init = importlib.import_module("agent.agent_init")
    snapshot = importlib.import_module("agent.primary_runtime_snapshot")
    auxiliary = importlib.import_module("agent.auxiliary_client")
    fallback_config = importlib.import_module("hermes_cli.fallback_config")
    for module in (init, snapshot, auxiliary, fallback_config):
        assert Path(module.__file__).resolve().is_relative_to(SOURCE)
    assert "run_agent" not in sys.modules
    # Owner attribution would query config/pool metadata. This component's
    # providers are literal built-in identities and have no custom owner.
    monkeypatch.setattr(snapshot, "runtime_entry_owner", lambda *args: None)
    return init, snapshot, auxiliary, fallback_config


def routed_agent(real_components, monkeypatch, primary_available):
    init, snapshot, auxiliary, fallback_config = real_components
    from agent.status_output import StatusOutputMixin

    class ComponentAgent(StatusOutputMixin, SimpleNamespace):
        pass

    agent = ComponentAgent(
        provider="openai", requested_provider="openai", model="primary-model",
        base_url=PRIMARY_URL, api_mode="chat_completions", api_key="",
        _client_kwargs={"api_key": "", "base_url": PRIMARY_URL},
        _fallback_activated=False, _use_prompt_caching=True,
        _use_native_cache_layout=False, _reasoning_echo_flag=True,
        request_overrides={},
        _is_azure_openai_url=lambda url: False,
        _is_direct_openai_url=lambda url: False,
        _provider_model_requires_responses_api=lambda model, **kwargs: False,
    )
    fallback = {"provider": "openrouter", "model": "fallback-model",
                "base_url": FALLBACK_URL, "api_key": "synthetic-fallback"}
    primary_client = SimpleNamespace(api_key="synthetic-primary", base_url=PRIMARY_URL)
    fallback_client = SimpleNamespace(api_key="synthetic-fallback", base_url=FALLBACK_URL)

    def resolver(provider, *, model, raw_codex, **kwargs):
        assert raw_codex is True
        if provider == "openai" and model == "primary-model" and not kwargs:
            return (primary_client if primary_available else None), "primary-model"
        assert not primary_available
        assert (provider, model, kwargs) == (
            "openrouter", "fallback-model",
            {"explicit_base_url": FALLBACK_URL, "explicit_api_key": "synthetic-fallback"},
        )
        return fallback_client, "fallback-resolved-model"

    def entry_key(entry):
        assert entry == fallback
        return "synthetic-fallback"

    monkeypatch.setattr(auxiliary, "resolve_provider_client", resolver)
    monkeypatch.setattr(fallback_config, "resolve_entry_api_key", entry_key)
    kwargs = init._routed_client_kwargs(agent, [fallback], 10)
    assert bool(getattr(agent, "_pending_fallback_notice", None)) is (not primary_available)
    # Explicit assignment boundary from _init_openai_client; no client creation.
    agent._client_kwargs = kwargs
    agent.api_key = kwargs["api_key"]
    agent.base_url = kwargs["base_url"]
    agent.context_compressor = SimpleNamespace(
        model=agent.model, provider=agent.provider, base_url=agent.base_url,
        api_key=agent.api_key, context_length=8192, threshold_tokens=4096,
    )
    snapshot._snapshot_primary_runtime(agent)

    assert agent._fallback_activated is (not primary_available)
    assert (agent.provider, agent.model, agent.base_url, agent.api_key) == (
        ("openai", "primary-model", PRIMARY_URL, "synthetic-primary")
        if primary_available else
        ("openrouter", "fallback-resolved-model", FALLBACK_URL, "synthetic-fallback")
    )
    return agent
