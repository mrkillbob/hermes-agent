"""A held boot caps malloc arenas without reading private config or ``.env``."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_cli import resource_limits


@pytest.mark.parametrize("held", [True, False])
def test_arena_cap_reads_config_only_when_not_held(monkeypatch, held):
    calls = []
    libc = SimpleNamespace(mallopt=lambda param, value: calls.append(value) or 1)
    monkeypatch.setattr(resource_limits, "glibc_malloc", lambda: libc)
    monkeypatch.setattr(resource_limits, "_arena_max_setting", lambda env: None)
    monkeypatch.setattr(resource_limits, "_launch_environ", lambda: None)
    monkeypatch.setattr("hermes_cli.startup_hold.defer_maintenance", lambda argv=None: held)
    loaded = []

    def load_config_readonly():
        loaded.append(True)
        return {"runtime": {"malloc_arena_max": 3}}

    monkeypatch.setattr("hermes_cli.config.load_config_readonly", load_config_readonly)

    assert resource_limits.apply_malloc_arena_max() is True
    assert loaded == ([] if held else [True])
    assert calls == [resource_limits.DEFAULT_MALLOC_ARENA_MAX if held else 3]
