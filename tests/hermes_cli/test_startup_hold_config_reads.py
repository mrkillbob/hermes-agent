"""Held config reads retain effective settings without unrelated home maintenance."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.platforms('any')


@pytest.fixture
def homes(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    managed = tmp_path / 'managed'
    home.mkdir()
    managed.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_MANAGED_DIR', str(managed))
    monkeypatch.setenv('FIXTURE_CACHE_SIZE', '-1234')
    from hermes_cli import config, config_effective, managed_scope
    config._LOAD_CONFIG_CACHE.clear()
    config._RAW_CONFIG_CACHE.clear()
    config_effective._EFFECTIVE_CACHE.clear()
    config_effective._LAST_GOOD_USER_RAW.clear()
    managed_scope.invalidate_managed_cache()
    (home / 'config.yaml').write_text(
        'database:\n  journal_mode: wal\n  synchronous: OFF\n'
        '  cache_size: ${FIXTURE_CACHE_SIZE}\n  temp_store: 2\n'
        'approvals:\n  deny: ["private-command*"]\n', encoding='utf-8')
    (managed / 'config.yaml').write_text(
        'database:\n  journal_mode: delete\n  synchronous: FULL\n', encoding='utf-8')
    return home, managed


def snapshot(home):
    return {p.relative_to(home).as_posix(): (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
            for p in home.rglob('*') if p.is_file()}


@pytest.mark.parametrize('legacy_soul', [False, True])
def test_held_session_database_keeps_settings_and_home_maintenance_untouched(homes, legacy_soul):
    home, _ = homes
    from hermes_cli.default_soul import _SCAFFOLD_HEAD, _SCAFFOLD_TAIL, is_legacy_template_soul
    from hermes_state import SessionDB
    if legacy_soul:
        soul = _SCAFFOLD_HEAD + _SCAFFOLD_TAIL
        assert is_legacy_template_soul(soul)
        (home / 'SOUL.md').write_text(soul, encoding='utf-8')
    (home / '.drain_request.json').write_text('{"startup_hold": {}}', encoding='utf-8')
    (home / 'config.yaml.bak.20200101').write_text('legacy backup', encoding='utf-8')
    backups = home / 'backups/config'
    backups.mkdir(parents=True)
    for index in range(7):
        (backups / f'config.yaml.good.2000010{index}').write_text(f'old: {index}\n', encoding='utf-8')
    before = snapshot(home)
    db = SessionDB(home / 'state.db')
    try:
        for pragma, expected in [('journal_mode', 'delete'), ('synchronous', 2),
                                 ('cache_size', -1234), ('temp_store', 2)]:
            assert db._conn.execute(f'PRAGMA {pragma}').fetchone()[0] == expected
    finally:
        db.close()
    after = snapshot(home)
    assert {key: after[key] for key in before} == before
    if not legacy_soul:
        assert not (home / 'SOUL.md').exists()
    assert set(after) - set(before) <= {
        'state.db', 'state.db.quarantine.lock', 'state.db.wal.lock',
        'state.db.fts_rebuild.lock', 'cache/schema_columns.json',
    }


@pytest.mark.parametrize('effective', [False, True])
def test_deferred_read_does_not_suppress_later_normal_maintenance(homes, monkeypatch, effective):
    home, _ = homes
    from hermes_cli.config import load_config_readonly
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.config_backups import list_config_backups
    reader = load_user_config_effective if effective else load_config_readonly
    monkeypatch.setattr('sys.argv', ['hermes', 'gateway', 'ensure'])
    first = reader()
    assert first['database']['journal_mode'] == 'delete'
    assert first['approvals']['deny'] == ['private-command*']
    assert not (home / 'SOUL.md').exists()
    assert not list_config_backups(home / 'config.yaml', 'good')
    if not effective:
        assert reader() is first
    monkeypatch.setattr('sys.argv', ['hermes', 'config', 'get'])
    normal = reader()
    assert normal == first
    assert len(list_config_backups(home / 'config.yaml', 'good')) == 1
    if not effective:
        assert (home / 'SOUL.md').exists()
        assert reader() is normal
        assert normal is not first


def test_held_named_profile_tombstone_still_refuses_without_recreation(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    root = tmp_path / '.hermes'
    root.mkdir()
    (root / '.drain_request.json').write_text('{"startup_hold": {}}', encoding='utf-8')
    profile = root / 'profiles/deleted'
    monkeypatch.setenv('HERMES_HOME', str(profile))
    from hermes_cli.config import load_config_readonly
    with pytest.raises(FileNotFoundError, match='Named profile home'):
        load_config_readonly()
    assert not profile.exists()


@pytest.mark.parametrize('effective', [False, True])
def test_held_corrupt_reads_keep_last_good_deny_rules_without_snapshotting(homes, monkeypatch, effective):
    home, _ = homes
    from hermes_cli.config import load_config_readonly
    from hermes_cli.config_read_errors import get_active_config_parse_failure
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.config_backups import list_config_backups
    reader = load_user_config_effective if effective else load_config_readonly
    marker = home / '.drain_request.json'
    marker.write_text('{"startup_hold": {}}', encoding='utf-8')
    reader()  # In-process last-good values, without writing a snapshot.
    config = home / 'config.yaml'
    config.write_text('approvals: [unclosed\n', encoding='utf-8')
    before = snapshot(home)
    fallback = reader()
    assert fallback['approvals']['deny'] == ['private-command*']
    from hermes_cli.config import atomic_config_write
    with pytest.raises(RuntimeError, match='not saved'):
        atomic_config_write(config, fallback)
    assert get_active_config_parse_failure()
    assert snapshot(home) == before
    marker.unlink()  # No boot latch in this loader-only fixture; supported native restore requires exit.
    assert reader()['approvals']['deny'] == ['private-command*']
    assert len(list_config_backups(config, 'corrupt')) == 1
    assert not list_config_backups(config, 'good')
