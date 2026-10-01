"""The E2E sandbox must not bootstrap an app generation for portable plugin toggles."""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest
import hermes_yaml as yaml


@pytest.fixture
def e2e_home(tmp_path, monkeypatch):
    # Importing the E2E helper normally snapshots PM's tool cache at collection.
    # This contract test needs only the dependency-state helper, so make that
    # import take its documented no-test-venv branch before supplying a temp venv.
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "no-runtime"))
    helper = importlib.import_module("tests.e2e.core._pm_dependencies")

    source = tmp_path / "test-python"
    source.mkdir()
    (source / "pyvenv.cfg").write_text("home = fixture\nversion = 3.14.7\n", encoding="utf-8")
    from pm.environments import site_packages
    site_packages(source).mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(source))

    home = Path(os.environ["HERMES_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    plugins = home / "plugins"
    plugins.mkdir(exist_ok=True)
    config = home / "config.yaml"
    config.write_text("plugins:\n  enabled: []\n  disabled: []\n", encoding="utf-8")
    plugin = plugins / "portable"
    plugin.mkdir()
    (plugin / "plugin.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "portable", "version": "1.0.0",
    }), encoding="utf-8")
    (plugin / "mcp.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")

    checkout = Path(__file__).resolve().parents[2]
    helper.select_test_dependencies(home, checkout)
    return home, plugins, config, checkout


def _admit(home, plugins, enabled, *, expected_config=None):
    from hermes_cli.plugins_admission import admit_plugin_set_change

    admit_plugin_set_change(
        set(enabled), set(), active_plugins_dir=plugins, expected_config=expected_config,
    )


def _use_engine_in_process(monkeypatch):
    # Keep the real admission and PM engine contracts while avoiding PM worker
    # bootstrap in this no-build boundary test.
    from pm import client
    from pm.install import sync_venv

    monkeypatch.setattr(client, "sync_venv", sync_venv)


def test_dependency_free_portable_enable_publishes_without_rebuild(e2e_home, monkeypatch):
    from hermes_cli.plugins_admission import AdmissionRefused
    from pm.environments import install_state_dir
    from pm.packages import Venv

    home, plugins, config, checkout = e2e_home
    _use_engine_in_process(monkeypatch)
    facts = install_state_dir(checkout) / "facts.json"
    original = facts.read_bytes()

    class RebuildAttempted(RuntimeError):
        pass

    attempts = []

    def refuse_rebuild(self, *args, **kwargs):
        attempts.append((args, kwargs))
        raise RebuildAttempted("offline sentinel: PM tried to build")

    monkeypatch.setattr(Venv, "apply", refuse_rebuild)
    # Reproduce the old partial fixture record: PM must reject it as stale and
    # reach the build boundary, before it can publish the proposed config.
    facts.write_text(json.dumps({"schema": 1, "packages": {"venv": {
        "environment": json.loads(original)["packages"]["venv"]["environment"],
    }}}), encoding="utf-8")
    with pytest.raises(AdmissionRefused, match="offline sentinel"):
        _admit(home, plugins, ["portable"])
    assert attempts
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["plugins"]["enabled"] == []

    # The fixed core-only stamp lets the same real Selection validation and
    # publication run without invoking Venv.apply for this non-member plugin.
    facts.write_bytes(original)
    attempts.clear()
    _admit(home, plugins, ["portable"])
    assert attempts == []
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["plugins"]["enabled"] == ["portable"]
    assert facts.read_bytes() == original, "a dependency-free enable must not replace the fixture's core stamp"


def test_python_member_still_reaches_build_boundary(e2e_home, monkeypatch):
    from hermes_cli.plugins_admission import AdmissionRefused
    from pm.packages import Venv

    home, plugins, config, _checkout = e2e_home
    _use_engine_in_process(monkeypatch)
    member = plugins / "python-member"
    member.mkdir()
    (member / "plugin.yaml").write_text("name: python-member\n", encoding="utf-8")
    (member / "pyproject.toml").write_text(
        '[project]\nname = "python-member"\nversion = "1.0.0"\n', encoding="utf-8")

    class RebuildAttempted(RuntimeError):
        pass

    attempts = []

    def refuse_rebuild(self, *args, **kwargs):
        attempts.append((args, kwargs))
        raise RebuildAttempted("offline sentinel: Python member changed the stamp")

    monkeypatch.setattr(Venv, "apply", refuse_rebuild)
    with pytest.raises(AdmissionRefused, match="offline sentinel"):
        _admit(home, plugins, ["python-member"])
    assert attempts
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["plugins"]["enabled"] == []


def test_invalid_or_stale_plugin_selection_is_refused_before_build(e2e_home, monkeypatch):
    from hermes_cli.plugins_admission import AdmissionRefused
    from pm.packages import Venv

    home, plugins, config, _checkout = e2e_home
    _use_engine_in_process(monkeypatch)
    invalid = plugins / "invalid"
    invalid.mkdir()
    (invalid / "plugin.yaml").write_text("name: invalid\nmanifest_version: 999\n", encoding="utf-8")
    attempts = []
    monkeypatch.setattr(Venv, "apply", lambda *a, **kw: attempts.append((a, kw)))

    with pytest.raises(AdmissionRefused, match="manifest_version"):
        _admit(home, plugins, ["invalid"])
    with pytest.raises(AdmissionRefused, match="changed"):
        _admit(home, plugins, ["portable"], expected_config="0" * 64)
    assert attempts == []
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["plugins"]["enabled"] == []


def test_toggle_timeout_keeps_the_host_diagnostic_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "no-runtime"))
    importlib.import_module("tests.e2e.core._pm_dependencies")
    from tests.e2e.core.mcp_plugins.test_plugin_activation import _toggle_on

    class Rpc:
        def call(self, *args, **kwargs):
            raise AssertionError("plugins.manage: no response within 180s")

    class Host:
        rpc = Rpc()
        cap = type("Capture", (), {"stderr": "x" * 2500})()

    with pytest.raises(AssertionError) as caught:
        _toggle_on(Host(), "portable")
    assert str(caught.value).startswith("plugins.manage: no response within 180s\nhost stderr:\n")
    assert str(caught.value).endswith("x" * 2000)
