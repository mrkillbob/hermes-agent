"""Native startup hold fences source/secret/plugin maintenance before ordinary boot."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway import run  # library seams load before each temporary hold exists


@pytest.fixture
def held(tmp_path, monkeypatch):
    from gateway import drain_startup
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(drain_startup, '_refuse_legacy_writers', lambda *args: None)
    monkeypatch.setattr(drain_startup, '_checkout_root', lambda: Path(os.environ['HERMES_HOME']))
    (tmp_path / 'ESTOP').write_text('retained pause')
    drain_startup.begin()
    return tmp_path


def forbidden(*args, **kwargs):
    pytest.fail('startup hold reached a forbidden side-effect port')


def test_hold_skips_dotenv_and_external_source_resolution(held, monkeypatch):
    from hermes_cli.env_loader import load_hermes_dotenv
    monkeypatch.delenv('STARTUP_HOLD_FIXTURE', raising=False)
    (held / '.env').write_text('STARTUP_HOLD_FIXTURE=private-fixture\n')
    assert load_hermes_dotenv(hermes_home=held, load_external_secrets=False) == []
    assert 'STARTUP_HOLD_FIXTURE' not in os.environ


def test_hold_launch_preparation_never_adopts_or_syncs(held, monkeypatch):
    from hermes_cli import venv_sync, post_update
    root = held / 'source'
    root.mkdir()
    (root / '.git').mkdir()
    (root / 'pyproject.toml').write_text('[project]\nname="fixture"\n')
    monkeypatch.delenv('HERMES_DISABLE_LAZY_INSTALLS', raising=False)
    monkeypatch.setattr(post_update, 'step_adopt_blessed_checkout', forbidden)
    assert venv_sync.prepare_launch(root, ['gateway', 'run']) is None
    assert not (root / 'install-stamp.json').exists()


def test_hold_gateway_config_never_materializes_platforms_or_profiles(held, monkeypatch):
    monkeypatch.setattr(run, 'load_gateway_config', forbidden)
    config = run.load_gateway_config_for_runner()
    assert config.platforms == {}
    assert config.multiplex_profiles is False


def test_hold_pairing_constructor_does_not_migrate_security_state(held, monkeypatch):
    from gateway import pairing
    monkeypatch.setattr(pairing, '_migrate_split_pairing_dirs', forbidden)
    store = pairing.PairingStore()
    assert not store._dir.exists()


def test_hold_config_bridge_does_not_discover_auxiliary_plugins(held, monkeypatch):
    from hermes_cli import plugins
    monkeypatch.setattr(plugins, 'get_plugin_auxiliary_tasks', forbidden)
    # This helper must return before consulting plugin-owned model surfaces.
    run._bridge_config_to_env({'auxiliary': {'fixture': {'model': 'fixture'}}})


def test_hold_access_preflight_defers_plugins_with_no_adapters(held, monkeypatch):
    from hermes_cli import plugins
    monkeypatch.setattr(plugins, 'discover_plugins', forbidden)
    assert run._own_policy_open_startup_violation(SimpleNamespace(platforms={})) is None


def test_hold_gateway_settings_do_not_read_provider_config(held, monkeypatch):
    from hermes_cli import config_effective
    monkeypatch.setattr(config_effective, 'load_user_config_effective', forbidden)
    assert run._load_gateway_config() == {}


def test_hold_ensure_never_starts_or_replaces_an_owner(held, monkeypatch):
    from hermes_cli import gateway_runtime as runtime, gateway_runtime_start as start
    monkeypatch.setattr(runtime, '_observe_replacing_stale', forbidden)
    monkeypatch.setattr(start, 'spawn_unmanaged_gateway', forbidden)
    result = runtime.ensure_gateway_runtime(held, timeout=1)
    assert result.state == 'draining'
    assert result.reason_code == 'startup_hold'
    assert result.endpoint is None


def test_hold_early_entry_retains_pending_source_recovery(held):
    from hermes_cli import startup_hold
    root = held / 'source'
    root.mkdir()
    marker = root / '.hermes-update-zip-swap'
    marker.write_text('pending fixture')
    with pytest.raises(SystemExit, match='pending recovery'):
        startup_hold.enter_boot(root, ['gateway', 'run'])
    assert marker.read_text() == 'pending fixture'


@pytest.mark.parametrize('argv', [['serve'], ['gateway', 'run', '--replace'], ['gateway', 'run', '--force'], ['gateway', 'drain', 'arm', '--window-id', 'x']])
def test_hold_refuses_unsupported_entry_before_bootstrap(held, argv):
    from hermes_cli import startup_hold
    with pytest.raises(SystemExit, match='startup_hold'):
        startup_hold.enter_boot(held / 'source', argv)


def test_held_dependency_activation_never_recovers_publication(held, monkeypatch):
    from contextlib import contextmanager
    from pm import environments
    from hermes_cli import runtime_state
    @contextmanager
    def lock(*args, **kwargs):
        yield True
    calls = []
    monkeypatch.setattr(environments, 'install_state_dir', lambda root: held)
    monkeypatch.setattr(environments, 'committed_venv', lambda root: None)
    monkeypatch.setattr(environments, '_require_own_dependencies', lambda root: None)
    monkeypatch.setattr(runtime_state, 'runtime_lock', lock)
    monkeypatch.setattr(runtime_state, 'recover_publication', lambda root: calls.append(root))
    environments.activate_dependencies(held)
    assert calls == [held]
    calls.clear()
    environments.activate_dependencies(held, allow_recovery=False)
    assert calls == []
