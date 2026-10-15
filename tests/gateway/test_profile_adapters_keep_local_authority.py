"""A served profile's LOCAL adapter belongs to its session authority, not to config.yaml."""
from pathlib import Path

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from tests.gateway.test_multiplex_adapter_registry import (
    _SecondaryRecoveryAdapter,
    _install_secondary_reconnect_context,
    _secondary_recovery_runner,
)


@pytest.mark.asyncio
async def test_profile_adapter_rescan_keeps_the_authority_local_adapter(monkeypatch):
    runner = _secondary_recovery_runner()
    local = _SecondaryRecoveryAdapter()
    local.platform = Platform.LOCAL
    runner._profile_adapters["reviewer"] = {Platform.LOCAL: local}
    _install_secondary_reconnect_context(monkeypatch, runner, _SecondaryRecoveryAdapter())
    monkeypatch.setattr("hermes_cli.env_loader.hydrate_profile_secret_sources", lambda h: {})
    monkeypatch.setattr(gateway_run, "_load_gateway_config", dict)
    monkeypatch.setattr(runner, "_snapshot_profile_busy_modes", lambda *a, **k: None)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)

    async def connect(adapter, platform):
        return True

    monkeypatch.setattr(runner, "_connect_initial_adapter_with_timeout", connect)
    await runner._start_one_profile_adapters("reviewer", Path("/profiles/reviewer"), {})

    assert runner._profile_adapters["reviewer"][Platform.LOCAL] is local
    assert not local.disconnected
