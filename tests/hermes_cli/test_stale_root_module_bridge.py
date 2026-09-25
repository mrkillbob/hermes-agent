"""Bridge for pre-hand-off ``hermes update``: a stale root ``utils`` must not kill restart.

Updaters up to v2026.9.14 finish the post-pull phases in the pre-pull interpreter and
purge only package prefixes, so root ``utils`` stays cached without ``file_signature``;
the restart phase's fresh ``hermes_cli.config`` import then died with
``cannot import name 'file_signature' from 'utils'``. Freshly imported hermes_cli code
drops the incomplete root cache first (``hermes_cli.stale_modules``).
"""

from __future__ import annotations

import importlib
import sys

import pytest

# Mirrors the post-pull purge of a pre-hand-off updater (v2026.9.14
# hermes_cli/update_cmd_maint.py): package prefixes only, root-level modules survive.
_PRE_HANDOFF_PURGE_PREFIXES = ("hermes_cli", "gateway", "tools", "tui_gateway", "agent")
_PRE_HANDOFF_PURGE_PROTECTED = {"hermes_cli", "hermes_cli.main", "hermes_cli.hermes_logging"}


@pytest.fixture
def pre_handoff_purge():
    """Evict what a pre-hand-off updater evicts after the pull; restore the original graph after."""
    saved: dict = {"utils": sys.modules.get("utils")}

    def _purge() -> None:
        for name in list(sys.modules):
            if name in _PRE_HANDOFF_PURGE_PROTECTED or name.startswith("hermes_cli.update_"):
                continue
            if name.split(".", 1)[0] in _PRE_HANDOFF_PURGE_PREFIXES:
                module = sys.modules.pop(name, None)
                if module is not None:
                    saved[name] = module

    yield _purge
    for name, module in saved.items():
        if module is not None:
            sys.modules[name] = module


@pytest.mark.parametrize("consumer", ["hermes_cli.config", "hermes_cli.managed_scope"])
def test_fresh_hermes_cli_import_heals_stale_utils_missing_file_signature(
    monkeypatch, pre_handoff_purge, consumer
):
    """Restart-phase shape: hermes_cli.* purged, root utils stale, consumer freshly imported."""
    import utils

    monkeypatch.delattr(utils, "file_signature")
    pre_handoff_purge()
    assert consumer not in sys.modules

    module = importlib.import_module(consumer)
    assert callable(module.file_signature)
    assert hasattr(sys.modules["utils"], "file_signature")


def test_drop_stale_root_modules_leaves_complete_utils_alone():
    import utils
    from hermes_cli.stale_modules import drop_stale_root_modules

    assert hasattr(utils, "file_signature")
    before = sys.modules["utils"]
    assert drop_stale_root_modules() == []
    assert sys.modules["utils"] is before


def test_drop_stale_root_modules_also_heals_stale_package_modules():
    import types
    from hermes_cli.stale_modules import drop_stale_root_modules

    names = ("hermes_cli.tools_config", "hermes_cli.config_migrations", "gateway.status")
    before = {name: sys.modules.pop(name, None) for name in names}
    try:
        for name in names:
            sys.modules[name] = types.ModuleType(name)
        stale_tools_config = sys.modules["hermes_cli.tools_config"]
        stale_tools_config._configurable_keys = lambda: set()
        # The 45→46 MCP migration imports this parser, added after some N-1
        # updater processes had already cached tools_config.
        assert not hasattr(stale_tools_config, "_parse_enabled_flag")
        stale_migrations = sys.modules["hermes_cli.config_migrations"]
        stale_migrations._migrate_to_46 = lambda *_args: None
        stale_migrations.MIGRATIONS = ((45, lambda *_args: None),)
        assert set(drop_stale_root_modules()) == set(names)
        assert all(name not in sys.modules for name in names)
    finally:
        for name in names:
            sys.modules.pop(name, None)
            if before[name] is not None:
                sys.modules[name] = before[name]


def test_drop_stale_root_modules_keeps_module_being_imported():
    import importlib.machinery
    import types
    from hermes_cli.stale_modules import drop_stale_root_modules

    name = "hermes_cli.tools_config"
    before = sys.modules.get(name)
    module = types.ModuleType(name)
    module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
    module.__spec__._initializing = True
    sys.modules[name] = module
    try:
        assert drop_stale_root_modules() == []
        assert sys.modules[name] is module
    finally:
        sys.modules.pop(name, None)
        if before is not None:
            sys.modules[name] = before
