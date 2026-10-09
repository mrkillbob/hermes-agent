"""Automatic migration must finish before updater success bookkeeping."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hermes_cli import gateway_migrate, update_cmd, update_cmd_fleet, update_cmd_fleet_verify, update_cmd_maint, update_receipt


@pytest.mark.parametrize("failure", ["hook_exception", "apply_returns_false"])
def test_failed_migration_writes_partial_receipt_and_keeps_pending_marker(tmp_path, monkeypatch, failure):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(update_cmd_fleet_verify, '_print_legacy_units_warning', lambda: None)
    monkeypatch.setattr(update_cmd_maint, '_refresh_dashboard_after_update', lambda **kw: set())
    monkeypatch.setattr(update_cmd, '_surviving_pre_update_serve_runtimes', lambda *a: [])
    monkeypatch.setattr(update_cmd_fleet_verify, '_collect_fleet_snapshot', lambda *a: [])
    monkeypatch.setattr(update_receipt, 'print_fleet_version_matrix', lambda *a: False)
    finalize = Mock()
    monkeypatch.setattr(update_receipt, 'finalize_update_receipt', finalize)
    failed_apply = None
    if failure == "hook_exception":
        monkeypatch.setattr(gateway_migrate, 'maybe_auto_migrate_after_update',
                            Mock(side_effect=RuntimeError('migration failed')))
    else:
        from hermes_cli import gateway_migrate_guards

        plan = SimpleNamespace(already_multiplexed=False, profiles=[object(), object()],
                               manifest=None, standalone_secondaries=[object()], blocked=False)
        monkeypatch.setattr(gateway_migrate, '_host_supports_migration', lambda: None)
        monkeypatch.setattr(gateway_migrate, '_default_home', lambda: tmp_path)
        monkeypatch.setattr(gateway_migrate, 'build_migration_plan', lambda: plan)
        monkeypatch.setattr(gateway_migrate, 'format_plan', lambda *a, **kw: [])
        monkeypatch.setattr(gateway_migrate_guards, 'auto_migration_opted_out', lambda home: False)
        monkeypatch.setattr(gateway_migrate_guards, 'auto_migration_blockers', lambda plan: [])
        failed_apply = Mock(return_value=False)
        monkeypatch.setattr(gateway_migrate, 'apply_migration', failed_apply)
    monkeypatch.setattr(update_cmd_fleet_verify, '_named_gateways_still_owed', lambda: False)
    monkeypatch.setattr(update_cmd_fleet_verify, '_record_owed_gateway_inventory', lambda *a: None)
    marker = tmp_path / 'fleet_restart_pending'
    marker.write_text('pending')
    restart = update_cmd_fleet._GatewayRestartOutcome(False, [], [], [], [], [], [], set())
    with pytest.raises(SystemExit) as exc:
        update_cmd_fleet_verify._verify_fleet_after_update(restart, _pre_update_plan=None, _windows_gateway_resume=None,
                                                   update_complete=True)
    assert exc.value.code == 1
    assert finalize.call_args.args == ('partial',)
    assert marker.exists()
    if failed_apply is not None:
        failed_apply.assert_called_once_with(plan)
