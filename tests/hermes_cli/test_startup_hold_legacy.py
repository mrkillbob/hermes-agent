"""Legacy native/standalone writers cannot evade root-scoped startup admission."""
import os
import subprocess

import pytest


def inventory(monkeypatch, command, env):
    from hermes_cli import dashboard_procs, main_dashboard
    pid = os.getpid() + 100_000
    monkeypatch.setattr(dashboard_procs, '_iter_process_table', lambda **kwargs: [(pid, command)])
    monkeypatch.setattr(dashboard_procs, '_pid_environ', lambda target: env)
    monkeypatch.setattr(main_dashboard, '_dashboard_cmdline_for_pid',
                        lambda target: ['hermes', '--profile', 'default', 'serve'])


@pytest.mark.parametrize('known', [True, False])
def test_legacy_native_before_profile_lock_refuses_creation_and_restoration(tmp_path, monkeypatch, known):
    from gateway import drain_startup
    from hermes_cli import dashboard_procs
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(drain_startup, '_checkout_root', lambda: tmp_path)
    monkeypatch.setattr(dashboard_procs, '_iter_process_table', lambda **kwargs: [])
    (tmp_path / 'ESTOP').touch()
    value = drain_startup.begin()
    marker = tmp_path / '.drain_request.json'
    journal = tmp_path / '.startup-hold.json'
    before = marker.read_bytes(), journal.read_bytes()
    inventory(monkeypatch, 'python -m gateway.run',
              {'HOME': str(tmp_path / 'account'), 'HERMES_HOME': str(tmp_path)} if known else None)
    reason = 'startup_hold_writers_busy' if known else 'startup_hold_writers_unknown'
    with pytest.raises(RuntimeError, match=reason):
        drain_startup.restore(hold_id=value['hold_id'])
    assert (marker.read_bytes(), journal.read_bytes()) == before
    marker.unlink()
    journal.unlink()  # reset only this synthetic temporary fixture to exercise creation too
    with pytest.raises(RuntimeError, match=reason):
        drain_startup.begin()
    assert not marker.exists() and not journal.exists()


@pytest.mark.platforms('posix')
def test_legacy_suffixed_default_root_is_not_foreign(tmp_path, monkeypatch):
    from hermes_cli import startup_hold_writers
    account = tmp_path / 'account'
    target = account / '.hermes-fixture'
    monkeypatch.setenv('HERMES_HOME', str(target))
    inventory(monkeypatch, 'hermes --profile default serve',
              {'HOME': str(account), 'HERMES_DATA_DIR_SUFFIX': '-fixture'})
    with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
        startup_hold_writers.refuse_legacy_writers()


def test_foreign_home_expansion_uses_target_environment(tmp_path, monkeypatch):
    from hermes_cli import startup_hold_writers
    target = tmp_path / 'custom'
    monkeypatch.setenv('HERMES_HOME', str(target))
    inventory(monkeypatch, 'hermes serve', {'HOME': str(tmp_path / 'account'),
        'HERMES_HOME': str(tmp_path) + '/${TARGET}/profiles/work', 'TARGET': 'custom'})
    with pytest.raises(RuntimeError, match='startup_hold_writers_busy'):
        startup_hold_writers.refuse_legacy_writers()


@pytest.mark.platforms('posix')
def test_lexical_profile_home_stays_in_its_original_root(tmp_path, monkeypatch):
    from hermes_cli import startup_hold_writers
    root = tmp_path / 'custom'
    profiles = root / 'profiles'
    profiles.mkdir(parents=True)
    actual = tmp_path / 'actual-profile'
    actual.mkdir()
    (profiles / 'work').symlink_to(actual, target_is_directory=True)
    monkeypatch.setenv('HERMES_HOME', str(root))
    assert startup_hold_writers._same_root(str(profiles / 'work'))


def test_gateway_status_is_not_a_native_writer(tmp_path, monkeypatch):
    from hermes_cli import startup_hold_writers
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    inventory(monkeypatch, 'python -m hermes_cli.main gateway status', None)
    startup_hold_writers.refuse_legacy_writers()


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('stdout', ['', 'unparseable process inventory'])
def test_empty_or_unparseable_windows_census_is_unknown(monkeypatch, stdout):
    from hermes_cli import dashboard_procs, _subprocess_compat
    monkeypatch.setattr(_subprocess_compat, 'bounded_probe_run',
                        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout, ''))
    with pytest.raises(RuntimeError, match='startup_hold_writers_unknown'):
        dashboard_procs._iter_process_table(strict=True)
