"""Bounded policies preserve required work and report incomplete adoption honestly."""
from pathlib import Path
import sqlite3

import pytest

from hermes_cli import left_core_migration as lcm, memory_provider_migration as memory
from hermes_cli import update_cmd_maint as maint, maintenance_policy as policy
from hermes_cli.config import read_user_config_raw


@pytest.fixture
def homes(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default = tmp_path / ".hermes"
    default.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(default))
    monkeypatch.setattr(lcm, "_attempted", set())
    monkeypatch.setattr(lcm, "_undelivered", {})
    return default


def configured(home, *, migration="defer", recovery="check-only"):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        f"toolsets: [homeassistant]\nupdates:\n  left_core_migration: {migration}\n"
        f"  state_db_recovery: {recovery}\n")
    (home / ".env").write_text("")
    return home


def database(path, value="snapshot"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("create table evidence(value text)")
        conn.execute("insert into evidence values(?)", (value,))


def forbidden(*args, **kwargs):
    pytest.fail("unapproved acquisition or recovery path ran")


def test_deferred_home_preserves_scope_without_catalog_or_retry_stamp(homes, monkeypatch):
    configured(homes)
    monkeypatch.setattr(memory, "catalog_source", forbidden)
    pending = []
    assert lcm._pending(homes, say=lambda message: None, pending=pending) == []
    raw = read_user_config_raw(homes / "config.yaml")
    assert raw["_left_core_scoped"] == ["homeassistant"]
    assert raw["known_plugin_toolsets"] == {"acp": ["homeassistant"], "webhook": ["homeassistant"]}
    assert "_left_core_installed" not in raw
    assert not (homes / "cache/left-core-homeassistant.failed").exists()
    assert pending[0][0] == "left_core_migration"
    before = (homes / "config.yaml").read_bytes()
    assert lcm._pending(homes, say=lambda message: None) == []
    assert (homes / "config.yaml").read_bytes() == before


def test_startup_and_update_use_each_homes_policy(homes, monkeypatch):
    configured(homes)
    sibling = configured(homes / "profiles/worker", migration="auto")
    import pm.plugins_state
    monkeypatch.setattr(pm.plugins_state, "dependency_homes", lambda: [homes, sibling])
    lookups, installed, said, pending = [], [], [], []
    monkeypatch.setattr(memory, "catalog_source", lambda name: lookups.append(name) or name)
    def install(home):
        def acquire(name):
            installed.append(home)
            (home / "plugins" / name).mkdir(parents=True)
            return {"ok": True}
        return acquire
    monkeypatch.setattr(lcm, "_install_into", install)
    assert lcm.migrate_all_homes(say=said.append, pending=pending) == ["homeassistant"]
    assert installed == [sibling] and lookups == ["homeassistant"]
    assert len(pending) == 1
    monkeypatch.setattr(memory, "catalog_source", forbidden)
    monkeypatch.setattr(lcm, "_install_into", forbidden)
    assert lcm.recover_at_startup(say=said.append) == []
    assert any("deferred" in message and "feature off" in message for message in said)


def test_releasing_deferral_runs_normal_installer_and_then_converges(homes, monkeypatch):
    configured(homes)
    import pm.plugins_state
    monkeypatch.setattr(pm.plugins_state, "dependency_homes", lambda: [homes])
    monkeypatch.setattr(memory, "catalog_source", forbidden)
    assert lcm.migrate_all_homes(say=lambda message: None, pending=[]) == []
    configured(homes, migration="auto")
    calls = []
    monkeypatch.setattr(memory, "catalog_source", lambda name: name)
    def acquire(name):
        calls.append(name)
        (homes / "plugins" / name).mkdir(parents=True)
        return {"ok": True}
    monkeypatch.setattr(lcm, "_install_into", lambda home: acquire)
    assert lcm.migrate_all_homes(say=lambda message: None) == ["homeassistant"]
    assert lcm.migrate_all_homes(say=lambda message: None) == []
    assert calls == ["homeassistant"]
    assert read_user_config_raw(homes / "config.yaml")["_left_core_installed"] == ["homeassistant"]


@pytest.mark.parametrize("value", ["wrong", "null", "true"])
def test_invalid_migration_policy_reports_pending_without_scope_or_catalog(homes, monkeypatch, value):
    configured(homes, migration=value)
    import pm.plugins_state
    monkeypatch.setattr(pm.plugins_state, "dependency_homes", lambda: [homes])
    monkeypatch.setattr(memory, "catalog_source", forbidden)
    pending = []
    assert lcm.migrate_all_homes(say=lambda message: None, pending=pending) == []
    assert pending and "must be one of" in pending[0][1]
    assert "_left_core_scoped" not in read_user_config_raw(homes / "config.yaml")


def test_check_only_never_searches_old_snapshots_or_deletes_sidecars(homes, monkeypatch):
    configured(homes)
    state = homes / "state.db"
    state.write_bytes(b"corrupt synthetic database" * 100)
    database(homes / "state-snapshots/20000101/state.db")
    paths = [state]
    for suffix in ("-wal", "-shm", "-journal"):
        path = homes / ("state.db" + suffix)
        path.write_bytes(b"synthetic sidecar")
        paths.append(path)
    before = {path: path.read_bytes() for path in paths}
    from hermes_cli import backup
    monkeypatch.setattr(backup, "_quick_snapshot_root", forbidden)
    monkeypatch.setattr(maint, "_restore_state_db_from_snapshot", forbidden)
    for _ in range(2):
        result = maint._verify_and_restore_one_state_db(homes, label="default")
        assert result["ok"] is False and result["policy"] == "check-only"
    assert {path: path.read_bytes() for path in paths} == before


def test_default_auto_recovery_keeps_existing_snapshot_selection(homes, monkeypatch):
    configured(homes, recovery="auto")
    state = homes / "state.db"
    state.write_bytes(b"corrupt synthetic database" * 100)
    database(homes / "state-snapshots/20000101/state.db")
    import hermes_cli.backup_restore as restore
    monkeypatch.setattr(restore, "_foreign_db_holder_pids", lambda path: [])
    result = maint._verify_and_restore_one_state_db(homes, label="default")
    assert result["ok"] is True and "20000101" in result["restored"]
    with sqlite3.connect(state) as conn:
        assert conn.execute("select value from evidence").fetchone() == ("snapshot",)


def test_sibling_health_is_reported_and_healthy_check_only_does_full_check(homes, monkeypatch):
    configured(homes)
    sibling = configured(homes / "profiles/worker")
    database(homes / "state.db", "current")
    (sibling / "state.db").write_bytes(b"broken" * 100)
    from hermes_cli import backup, update_cmd as cmd
    monkeypatch.setattr(cmd, "get_hermes_home", lambda: homes)
    monkeypatch.setattr(backup, "_sibling_profile_homes", lambda home, **kwargs: [("worker", sibling)])
    calls = []
    real = backup.verify_sqlite_integrity
    def verify(path, **kwargs):
        calls.append(kwargs)
        return real(path, **kwargs)
    monkeypatch.setattr(backup, "verify_sqlite_integrity", verify)
    pending = maint._verify_and_restore_state_dbs_post_update()
    assert len(pending) == 1 and "worker" in pending[0][1]
    assert all(kwargs["max_bytes"] == 0 for kwargs in calls)


def test_verification_error_and_invalid_recovery_policy_are_unhealthy(homes, monkeypatch):
    configured(homes)
    database(homes / "state.db")
    from hermes_cli import backup
    monkeypatch.setattr(backup, "verify_sqlite_integrity", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("fixture I/O error")))
    assert "fixture I/O error" in maint._verify_and_restore_one_state_db(homes, label="home")["error"]
    configured(homes, recovery="invalid")
    assert "must be one of" in maint._verify_and_restore_one_state_db(homes, label="home")["error"]


def completion_dependencies(monkeypatch, homes):
    import hermes_cli.update_cmd as cmd
    monkeypatch.setattr(cmd, "get_hermes_home", lambda: homes)
    monkeypatch.setattr(cmd, "_check_and_apply_config_migration", lambda **kwargs: None)
    from hermes_cli import macos_tcc_anchor as anchor, gitlock
    import hermes_cli.model_catalog as models
    monkeypatch.setattr(anchor, "ensure_tcc_anchor", lambda: None)
    monkeypatch.setattr(gitlock, "fetch_full_commit_graph", lambda *args, **kwargs: False)
    monkeypatch.setattr(models, "seed_cache_from_checkout", lambda *args: False)
    for name in ("_invalidate_live_plugin_catalog_caches", "_print_bundled_skills_sync_report", "_sync_profiles_after_update", "_print_post_update_notices_and_self_heals"):
        monkeypatch.setattr(maint, name, lambda: None)
    monkeypatch.setattr(maint, "_verify_and_restore_state_dbs_post_update", lambda: [("state_db_health", "worker database unhealthy")])
    monkeypatch.setattr(maint, "_post_update_sqlite_runtime_status", lambda: (True, object()))
    monkeypatch.setattr(maint, "_print_verified_update_completion", forbidden)
    monkeypatch.setattr(cmd, "_run_post_update_maintenance", maint._run_post_update_maintenance)
    import hermes_cli.update_receipt as receipts
    actions = []
    monkeypatch.setattr(receipts, "record_user_action", lambda step, reason: actions.append(step))
    monkeypatch.setattr(receipts, "record_followup", lambda *args, **kwargs: None)
    return actions


def test_both_completion_paths_keep_pending_visible_without_owing_products(homes, monkeypatch, capsys):
    configured(homes)
    actions = completion_dependencies(monkeypatch, homes)
    from hermes_cli import source_completion, source_build, source_stamp, venv_sync, update_cmd as cmd
    from hermes_cli.update_finish import finish_update
    from hermes_cli.update_receipt import TAIL_FOLLOWUPS
    root = homes.parent / "owned-source"
    root.mkdir()
    monkeypatch.setattr(venv_sync, "publish_launchers", lambda root: None)
    monkeypatch.setattr(source_build, "build_update_products", lambda *args, **kwargs: [("left_core_migration", "homeassistant deferred")])
    stamped = []
    monkeypatch.setattr(source_stamp, "write_source_stamp", lambda root: stamped.append(root))
    monkeypatch.setattr(venv_sync, "clear_completion", lambda root: None)
    monkeypatch.setattr(cmd, "_restart_gateway_fleet_after_update", lambda *args: object())
    monkeypatch.setattr(cmd, "_resume_windows_gateways_and_merge_outcome", lambda *args: None)
    verified = []
    monkeypatch.setattr(cmd, "_verify_fleet_after_update", lambda *args, **kwargs: verified.append(kwargs["update_complete"]))
    for _ in range(2):
        followups = []
        assert source_completion.complete_source_checkout(root, desktop=False, assume_yes=True, followups=followups) is False
        assert {step for step, _ in followups} == {"left_core_migration", "state_db_health"}
        assert not any(step in TAIL_FOLLOWUPS for step, _ in followups)
    followups = []
    finish_update(root=root, assume_yes=True, gateway_mode=False, pre_update_snapshot_id=None,
                  had_desktop_app_before_update=False, pre_update_version=None, plan=None,
                  windows_resume=None, followups=followups, pending=[("left_core_migration", "homeassistant deferred")])
    assert len(stamped) == 3 and verified == [False]
    assert actions.count("left_core_migration") == 3 and actions.count("state_db_health") == 3
    output = capsys.readouterr().out
    assert "maintenance requires user action" in output.lower()
    assert "✓ Install complete" not in output and "✓ Update complete" not in output


def test_product_failure_keeps_deferred_outcomes_for_both_completion_callers(homes, monkeypatch):
    configured(homes)
    actions = completion_dependencies(monkeypatch, homes)
    monkeypatch.setattr(maint, "_verify_and_restore_state_dbs_post_update", list)
    from hermes_cli import source_build, source_completion, source_stamp, venv_sync, update_cmd as cmd
    import pm.plugins_state
    monkeypatch.setattr(pm.plugins_state, "dependency_homes", lambda: [homes])
    monkeypatch.setattr(memory, "catalog_source", forbidden)
    monkeypatch.setattr(memory, "migrate_all_homes", list)
    monkeypatch.setattr(source_build, "source_frontends", lambda root: ())
    monkeypatch.setattr("hermes_cli.main_install_repair._install_configured_features_missing_deps", lambda root: (_ for _ in ()).throw(RuntimeError("synthetic product failure")))
    with pytest.raises(source_build.ProductBuildError) as failure:
        source_build.build_update_products(homes, desktop=False)
    assert failure.value.pending and failure.value.pending[0][0] == "left_core_migration"
    monkeypatch.setattr(venv_sync, "publish_launchers", lambda root: None)
    monkeypatch.setattr(source_stamp, "write_source_stamp", forbidden)
    followups = []
    assert source_completion.complete_source_checkout(homes, desktop=False, assume_yes=True, followups=followups) is False
    assert {step for step, _ in followups} == {"build", "left_core_migration"}
    from hermes_cli.update_finish import finish_update
    monkeypatch.setattr(cmd, "_restart_gateway_fleet_after_update", lambda *args: object())
    monkeypatch.setattr(cmd, "_resume_windows_gateways_and_merge_outcome", lambda *args: None)
    monkeypatch.setattr(cmd, "_verify_fleet_after_update", lambda *args, **kwargs: None)
    finish_update(root=homes, assume_yes=True, gateway_mode=False, pre_update_snapshot_id=None,
                  had_desktop_app_before_update=False, pre_update_version=None, plan=None,
                  windows_resume=None, followups=[("build", "synthetic product failure")], pending=failure.value.pending)
    assert actions.count("left_core_migration") == 2


def test_real_sibling_enumeration_error_is_an_unresolved_health_result(homes, monkeypatch):
    configured(homes)
    root = homes / "profiles"
    root.mkdir()
    sweep = maint._verify_and_restore_state_dbs_post_update
    completion_dependencies(monkeypatch, homes)
    monkeypatch.setattr(maint, "_verify_and_restore_state_dbs_post_update", sweep)
    real_iterdir = Path.iterdir
    def enumerate_paths(path):
        if path == root:
            raise PermissionError("synthetic inaccessible profiles")
        return real_iterdir(path)
    monkeypatch.setattr(Path, "iterdir", enumerate_paths)
    followups = []
    assert maint._run_post_update_maintenance(assume_yes=True, gateway_mode=False,
        pre_update_snapshot_id=None, had_desktop_app_before_update=False, pre_update_version=None,
        followups=followups) is False
    assert followups[0][0] == "state_db_health"
    assert "synthetic inaccessible profiles" in followups[0][1]


def test_cancelled_migration_reports_every_unfinished_home(homes, monkeypatch):
    configured(homes, migration="auto")
    sibling = configured(homes / "profiles/worker", migration="auto")
    import pm.plugins_state
    monkeypatch.setattr(pm.plugins_state, "dependency_homes", lambda: [homes, sibling])
    monkeypatch.setattr(memory, "catalog_source", lambda name: name)
    def cancel(name):
        raise KeyboardInterrupt
    monkeypatch.setattr(lcm, "_install_into", lambda home: cancel)
    pending = []
    assert lcm.migrate_all_homes(say=lambda message: None, pending=pending) == []
    assert len(pending) == 2 and all("cancelled" in reason for _, reason in pending)


def test_effective_policy_handles_home_alternation_and_managed_overlay(homes, monkeypatch):
    configured(homes, migration="auto")
    sibling = configured(homes / "profiles/worker")
    for home, expected in ((homes, "auto"), (sibling, "defer"), (homes, "auto")):
        assert policy.maintenance_policy(home, "left_core_migration", ("auto", "defer")) == expected
    managed = homes.parent / "managed"
    managed.mkdir()
    (managed / "config.yaml").write_text("updates:\n  left_core_migration: defer\n")
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    assert policy.maintenance_policy(homes, "left_core_migration", ("auto", "defer")) == "defer"
    (managed / "config.yaml").write_text("updates: [broken\n")
    with pytest.raises(Exception):
        policy.maintenance_policy(homes, "left_core_migration", ("auto", "defer"))


def test_wal_check_only_reads_current_rows_without_recovery(homes, monkeypatch):
    configured(homes)
    state = homes / "state.db"
    writer = sqlite3.connect(state)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("create table evidence(value text)")
        writer.execute("insert into evidence values('current WAL row')")
        writer.commit()
        database(homes / "state-snapshots/20000101/state.db", "obsolete row")
        paths = [state, homes / "state.db-wal", homes / "state.db-shm"]
        before = {path: path.read_bytes() for path in paths}
        monkeypatch.setattr(maint, "_restore_state_db_from_snapshot", forbidden)
        result = maint._verify_and_restore_one_state_db(homes, label="WAL fixture")
        assert result == {"ok": True, "policy": "check-only"}
        # Read-only SQLite can update SHM bookkeeping; it must preserve the main/WAL images.
        assert state.read_bytes() == before[state]
        assert paths[1].read_bytes() == before[paths[1]]
        assert paths[2].exists()
        assert writer.execute("select value from evidence").fetchone() == ("current WAL row",)
    finally:
        writer.close()


def test_inaccessible_database_metadata_is_unhealthy(homes, monkeypatch):
    configured(homes)
    state = homes / "state.db"
    real_stat = Path.stat
    def stat_path(path, **kwargs):
        if path == state:
            raise PermissionError("synthetic denied database metadata")
        return real_stat(path, **kwargs)
    monkeypatch.setattr(Path, "stat", stat_path)
    result = maint._verify_and_restore_one_state_db(homes, label="default")
    assert result["ok"] is False and "denied database metadata" in result["error"]


def test_strict_profile_inventory_cannot_hide_unavailable_metadata(homes, monkeypatch):
    configured(homes)
    sibling = homes / "profiles/worker"
    sibling.mkdir(parents=True)
    from hermes_cli.backup import _sibling_profile_homes
    real_stat = Path.stat
    def stat_path(path, **kwargs):
        if path == sibling:
            raise PermissionError("synthetic denied profile metadata")
        return real_stat(path, **kwargs)
    monkeypatch.setattr(Path, "stat", stat_path)
    with pytest.raises(PermissionError, match="denied profile metadata"):
        _sibling_profile_homes(homes, strict=True)
    # Legacy snapshot callers retain their non-raising inventory contract.
    assert _sibling_profile_homes(homes) == [("worker", sibling)]


def test_startup_deferral_precedes_catalog_lookup(homes, monkeypatch):
    configured(homes)
    monkeypatch.setattr(memory, "catalog_source", forbidden)
    monkeypatch.setattr(lcm, "_install_into", forbidden)
    said = []
    assert lcm.recover_at_startup(say=said.append) == []
    assert any("deferred" in message for message in said)
    assert "_left_core_installed" not in read_user_config_raw(homes / "config.yaml")


def test_existing_cua_deferral_precedes_pm_reads_and_records_skip(homes, monkeypatch):
    configured(homes)
    path = homes / "config.yaml"
    path.write_text(path.read_text().replace("updates:\n", "updates:\n  refresh_cua_driver: false\n"))
    import pm, hermes_cli.update_receipt as receipts
    monkeypatch.setattr(pm, "installed_package", forbidden)
    skipped = []
    monkeypatch.setattr(receipts, "record_skip", lambda step, reason: skipped.append((step, reason)))
    maint._refresh_cua_driver_after_update()
    assert skipped == [("cua_driver_refresh", "updates.refresh_cua_driver=false: host setup deferred")]
