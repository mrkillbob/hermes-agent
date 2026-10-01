"""profiles.describe / profiles.configure use the MCP ``enabled`` key the runtime reads.

Every runtime resolver (``enabled_mcp_server_names``, coding_context, oneshot, the gateway's
tool-name resolver) keys an MCP server's on/off state off ``mcp_servers.<name>.enabled``. The
profile editor used to read and write a separate ``disabled`` key, so a server toggled off in
the editor stayed live at runtime and a server with ``enabled: false`` showed as on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import hermes_yaml as yaml

import tui_gateway.server as server


@pytest.fixture
def profile_dir(tmp_path, monkeypatch) -> Path:
    """A temp HERMES_HOME root with one named profile, ``bot``, at ``<root>/profiles/bot``."""
    root = tmp_path / "hermes_home"
    path = root / "profiles" / "bot"
    path.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    return path


def _write_mcp(profile_dir: Path, servers: dict) -> None:
    (profile_dir / "config.yaml").write_text(yaml.safe_dump({"mcp_servers": servers}), encoding="utf-8")


def _read_mcp(profile_dir: Path) -> dict:
    return yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))["mcp_servers"]


def _call(method: str, params: dict) -> dict:
    resp = server._methods[method](1, {"name": "bot", **params})
    assert "error" not in resp, resp.get("error")
    return resp["result"]


def _described() -> dict:
    return {s["name"]: s["enabled"] for s in _call("profiles.describe", {})["mcp_servers"]}


def test_describe_matches_the_runtime_once_legacy_disabled_is_migrated(profile_dir):
    """Describe reads ``enabled``; a legacy ``disabled: true`` reads as off, and the config
    migration turns it into the ``enabled: false`` the runtime resolver honours."""
    from hermes_cli.config_migrations import run_migrations
    from hermes_cli.tools_config import enabled_mcp_server_names
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    _write_mcp(profile_dir, {
        "on": {"command": "on", "enabled": True},
        "off": {"command": "off", "enabled": False},
        # `hermes mcp add` writes `enabled: true`; the old editor then only added `disabled: true`.
        "legacy-off": {"command": "legacy", "enabled": True, "disabled": True},
        "implicit-on": {"command": "implicit"},
    })
    expected = {"on": True, "off": False, "legacy-off": False, "implicit-on": True}
    assert _described() == expected

    token = set_hermes_home_override(profile_dir)
    try:
        run_migrations(45, {"env_added": [], "config_added": [], "warnings": []}, quiet=True)
    finally:
        reset_hermes_home_override(token)

    live = enabled_mcp_server_names({"mcp_servers": _read_mcp(profile_dir)})
    assert {name: name in live for name in expected} == expected == _described()


def test_configure_toggle_is_what_the_runtime_resolver_and_describe_see(profile_dir):
    from hermes_cli.tools_config import enabled_mcp_server_names

    _write_mcp(profile_dir, {
        "keep": {"command": "keep", "enabled": False},
        "drop": {"command": "drop"},
        "legacy": {"command": "legacy", "disabled": True},
    })

    _call("profiles.configure", {"enabled_mcp_servers": ["keep", "legacy"]})

    on_disk = _read_mcp(profile_dir)
    assert not any("disabled" in entry for entry in on_disk.values())
    assert enabled_mcp_server_names({"mcp_servers": on_disk}) == {"keep", "legacy"}
    assert _described() == {"keep": True, "drop": False, "legacy": True}


def test_configure_replaces_legacy_mcp_flags_without_canonicalizing_unrelated_settings(profile_dir, monkeypatch):
    """An editor toggle deliberately deletes the legacy flag, preserving raw profile data."""
    import copy
    from hermes_cli import config
    from hermes_cli.tools_config import enabled_mcp_server_names
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    monkeypatch.setenv("PROFILE_MCP_TEST_TOKEN", "synthetic-token")
    raw = {
        "mcp_servers": {
            "legacy": {"command": "legacy", "disabled": True, "env": {"TOKEN": "${PROFILE_MCP_TEST_TOKEN}"}},
            "other": {"command": "other", "enabled": True},
        },
        "custom_setting": {"empty": {}, "template": "${PROFILE_MCP_TEST_TOKEN}"},
        "compression": {"extra_body": {}},
        "plugins": {"entries": {"untouched": {"settings": None}}},
    }
    path = profile_dir / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    expected = copy.deepcopy(raw)
    expected["mcp_servers"]["legacy"].pop("disabled")
    expected["mcp_servers"]["legacy"]["enabled"] = True
    expected["mcp_servers"]["other"]["enabled"] = False

    result = _call("profiles.configure", {"enabled_mcp_servers": ["legacy"]})
    assert result["applied"]["mcp_servers"] is True
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == expected
    assert _described() == {"legacy": True, "other": False}
    token = set_hermes_home_override(profile_dir)
    try:
        loaded = config.load_config()
        assert enabled_mcp_server_names(loaded) == {"legacy"}
        assert loaded["mcp_servers"]["legacy"]["env"]["TOKEN"] == "synthetic-token"
    finally:
        reset_hermes_home_override(token)


def test_configure_section_replacement_still_refuses_managed_keys(profile_dir, monkeypatch):
    from hermes_cli import config

    monkeypatch.setattr(config, "is_managed", lambda: False)
    monkeypatch.setattr(config.managed_scope, "is_key_managed", lambda key: key == "mcp_servers")
    _write_mcp(profile_dir, {"legacy": {"command": "legacy", "disabled": True}})
    path = profile_dir / "config.yaml"
    before = path.read_bytes()

    result = _call("profiles.configure", {"enabled_mcp_servers": ["legacy"]})
    assert result["applied"]["mcp_servers"] is False
    assert path.read_bytes() == before


def test_configure_section_replacement_refuses_to_erase_concurrent_unrelated_setting(profile_dir, monkeypatch):
    """Section replacement cannot authorize loss outside that section's tuple path."""
    import copy
    from hermes_cli import config

    path = profile_dir / "config.yaml"
    raw = {"mcp_servers": {"legacy": {"command": "legacy", "disabled": True}}}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    latest = copy.deepcopy(raw)
    latest["custom_setting"] = {"keep": "concurrent writer"}
    latest_bytes = yaml.safe_dump(latest).encode("utf-8")
    real_read = config.require_readable_config_before_write
    captured = False

    def add_setting_after_snapshot(config_path=None):
        nonlocal captured
        snapshot = real_read(config_path)
        if Path(config_path or config.get_config_path()) == path and not captured:
            captured = True
            path.write_bytes(latest_bytes)
        return snapshot

    monkeypatch.setattr(config, "require_readable_config_before_write", add_setting_after_snapshot)
    result = _call("profiles.configure", {"enabled_mcp_servers": ["legacy"]})
    assert result["applied"]["mcp_servers"] is False
    assert path.read_bytes() == latest_bytes
