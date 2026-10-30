"""Mirrored and air-gapped networks configure indexes through pip or uv; PM forwards
index settings and install concurrency while refusing ambient selection settings."""
from __future__ import annotations

import os
import subprocess

import pytest

from pm.environment import PythonEnvironment, _base_environment
from pm.package import InstallError


@pytest.fixture
def clean_index_env(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith(("UV_", "PIP_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    return tmp_path


def test_pip_index_reaches_uv_but_ambient_uv_selection_does_not(clean_index_env, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://mirror.example/simple")
    monkeypatch.setenv("PIP_TRUSTED_HOST", "mirror.example")
    monkeypatch.setenv("UV_HTTP_TIMEOUT", "300")
    monkeypatch.setenv("UV_INDEX_CORP_PASSWORD", "s3cret")
    monkeypatch.setenv("UV_PYTHON", "/poison/python")
    monkeypatch.setenv("UV_CACHE_DIR", "/poison/cache")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/poison/venv")

    env = _base_environment()

    assert env["UV_INDEX_URL"] == "https://mirror.example/simple"
    assert env["UV_INSECURE_HOST"] == "mirror.example"
    assert env["UV_HTTP_TIMEOUT"] == "300"
    assert env["UV_INDEX_CORP_PASSWORD"] == "s3cret"
    assert not {"UV_PYTHON", "UV_CACHE_DIR", "UV_PROJECT_ENVIRONMENT"} & env.keys()


def test_pip_conf_is_bridged_only_when_uv_has_no_index(clean_index_env, monkeypatch):
    pip_conf = clean_index_env / "pip.conf"
    # Percent-encoded credentials: pip reads its config raw, so must the bridge.
    pip_conf.write_text("[global]\nindex-url = https://user:p%40ss@mirror.example/simple\n", encoding="utf-8")
    monkeypatch.setenv("PIP_CONFIG_FILE", str(pip_conf))

    assert _base_environment()["UV_INDEX_URL"] == "https://user:p%40ss@mirror.example/simple"

    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://explicit.example/simple")
    env = _base_environment()
    assert env["UV_DEFAULT_INDEX"] == "https://explicit.example/simple"
    assert "UV_INDEX_URL" not in env


def test_streamed_runs_do_not_request_uv_debug_output(tmp_path, monkeypatch):
    import io
    from pm import environment

    monkeypatch.setenv("HERMES_VERBOSE", "1")

    seen: list[list[str]] = []
    kwargs_seen: list[dict] = []

    def record(command, **kwargs):
        seen.append(command)
        kwargs_seen.append(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(environment, "_run_streaming", record)
    PythonEnvironment(uv=tmp_path / "uv", python=tmp_path / "python", destination=tmp_path / "venv",
                      cache=tmp_path / "cache", env={}, output=io.StringIO())._run(["sync"], cwd=tmp_path, timeout=5)
    (command,), (kwargs,) = seen, kwargs_seen
    # Verbose only where the build backend speaks; uv's own DEBUG stays silent.
    assert "--verbose" in command and kwargs["env"]["RUST_LOG"] == "uv_build_frontend=debug"


def test_uv_timeout_names_the_mirror_knobs(tmp_path, monkeypatch):
    def stall(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", stall)
    environment = PythonEnvironment(uv=tmp_path / "uv", python=tmp_path / "python",
                                    destination=tmp_path / "venv", cache=tmp_path / "cache", env={})
    with pytest.raises(InstallError, match="UV_INDEX_URL") as info:
        environment._run(["sync"], cwd=tmp_path, timeout=7)
    assert "timed out after 7s" in str(info.value)


@pytest.mark.parametrize("no_config", [False, True])
def test_install_concurrency_reaches_child_without_steering_selection(tmp_path, no_config):
    """The PM bootstrap bounds uv compiler workers while keeping selected paths isolated."""
    import sys
    from pathlib import Path

    ambient = {
        "UV_CONCURRENT_INSTALLS": "1",
        "UV_INDEX_URL": "https://fixture.invalid/simple",
        "UV_HTTP_TIMEOUT": "99",
        "UV_PYTHON": "poison-python",
        "UV_CACHE_DIR": "poison-cache",
        "UV_PROJECT_ENVIRONMENT": "poison-project",
        "UV_CONFIG_FILE": "poison-config",
    }
    environment = PythonEnvironment(
        uv=Path(sys.executable), python=Path(sys.executable),
        destination=tmp_path / "venv", cache=tmp_path / "cache",
        env=ambient, no_config=no_config,
    )
    child = (
        "import os\n"
        "assert os.environ['UV_CONCURRENT_INSTALLS'] == '1'\n"
        f"assert ('UV_INDEX_URL' in os.environ) == {not no_config!r}\n"
        "assert os.environ['UV_HTTP_TIMEOUT'] == '99'\n"
        "assert 'UV_CONFIG_FILE' not in os.environ\n"
        f"assert os.environ['UV_PYTHON'] == {str(environment.python)!r}\n"
        f"assert os.environ['UV_CACHE_DIR'] == {str(environment.cache)!r}\n"
        f"assert os.environ['UV_PROJECT_ENVIRONMENT'] == {str(environment.destination)!r}\n"
    )
    result = environment._run(["-I", "-B", "-c", child], cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
