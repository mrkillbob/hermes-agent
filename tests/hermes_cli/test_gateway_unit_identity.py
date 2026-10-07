"""Installed systemd unit ownership remains the gateway service naming authority."""

import os
from pathlib import Path

import pytest
import hermes_constants
import hermes_cli.gateway as gateway_cli


class TestUnitAnchoredServiceIdentity:
    """The installed ``hermes-gateway.service`` owns the bare name: under ``sudo`` the naming basis moves
    mid-command when ``_sync_hermes_home_from_systemd_unit()`` adopts the unit's HERMES_HOME (#108674).

    ``platforms("linux")`` because ``_bare_unit_pinned_home()`` is Linux- and root-gated on purpose: a systemd unit
    is not an identity authority for launchd labels, Windows tasks, or s6 slots, which share the same
    resolver, and only an elevated process operates the system unit.
    """

    @pytest.mark.platforms("linux")
    def test_home_not_pinned_by_unit_keeps_its_suffix(self, tmp_path, monkeypatch):
        alice_home = tmp_path / "alice" / ".hermes"
        alice_home.mkdir(parents=True)
        bob_home = tmp_path / "bob" / ".hermes"
        bob_home.mkdir(parents=True)
        root_home = tmp_path / "root" / ".hermes"
        root_home.mkdir(parents=True)
        unit_dir = tmp_path / "systemd"
        unit_dir.mkdir()
        (unit_dir / f"{gateway_cli._SERVICE_BASE}.service").write_text(
            f'[Service]\nEnvironment="HERMES_HOME={alice_home}"\n', encoding="utf-8"
        )
        monkeypatch.setattr(gateway_cli, "_SYSTEM_UNIT_DIR", unit_dir)
        monkeypatch.setattr(hermes_constants, "_get_platform_default_hermes_home", lambda: root_home)
        monkeypatch.setenv("HERMES_HOME", str(bob_home))
        name = gateway_cli.get_service_name()
        assert name != gateway_cli._SERVICE_BASE
        assert name.startswith(gateway_cli._SERVICE_BASE + "-")

    @pytest.mark.platforms("linux")
    def test_unprivileged_profile_command_ignores_the_system_unit(self, tmp_path, monkeypatch):
        """A bare system unit pinning ``profiles/<name>`` must not alias that profile onto the user's
        default unit when an unprivileged user-scope command resolves the name."""
        profile_home = tmp_path / "alice" / ".hermes" / "profiles" / "kimi"
        profile_home.mkdir(parents=True)
        unit_dir = tmp_path / "systemd"
        unit_dir.mkdir()
        (unit_dir / f"{gateway_cli._SERVICE_BASE}.service").write_text(
            f'[Service]\nEnvironment="HERMES_HOME={profile_home}"\n', encoding="utf-8"
        )
        monkeypatch.setattr(gateway_cli, "_SYSTEM_UNIT_DIR", unit_dir)
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "alice")
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
        monkeypatch.setenv("HERMES_HOME", str(profile_home))
        assert gateway_cli.get_service_name() == "hermes-gateway-kimi"

    @pytest.mark.platforms("linux")
    def test_bare_unit_pinning_a_named_profile_home_keeps_the_bare_name(self, tmp_path, monkeypatch):
        """``sudo ... install --system`` names the unit from root's default but pins the invoking user's
        remapped home, so the BARE unit legitimately carries a ``profiles/<name>`` home. The unit-pinned
        check therefore has to win over the profile branch, which would answer ``-kimi`` for a unit that
        was installed bare."""
        profile_home = tmp_path / "alice" / ".hermes" / "profiles" / "kimi"
        profile_home.mkdir(parents=True)
        root_home = tmp_path / "root" / ".hermes"
        root_home.mkdir(parents=True)
        unit_dir = tmp_path / "systemd"
        unit_dir.mkdir()
        unit_path = unit_dir / f"{gateway_cli._SERVICE_BASE}.service"
        unit_path.write_text(f'[Service]\nEnvironment="HERMES_HOME={profile_home}"\n', encoding="utf-8")
        monkeypatch.setattr(gateway_cli, "_SYSTEM_UNIT_DIR", unit_dir)
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr(hermes_constants, "_get_platform_default_hermes_home", lambda: root_home)
        monkeypatch.setenv("HERMES_HOME", str(profile_home))
        assert gateway_cli.get_service_name() == gateway_cli._SERVICE_BASE
        # The profile branch, consulted against the home that owns the profile, would have answered
        # with the readable suffix -- which is why the unit-pinned check has to be evaluated first.
        assert gateway_cli._profile_name_from_home(profile_home, profile_home.parent.parent) == profile_home.name

    @pytest.mark.platforms("linux")
    def test_real_unit_sync_keeps_the_name_it_validated(self, tmp_path, monkeypatch):
        """Drive the production sync instead of simulating the adoption with setenv: the name resolved
        before ``_sync_hermes_home_from_systemd_unit()`` must survive the mutation it performs."""
        alice_home = tmp_path / "alice" / ".hermes"
        alice_home.mkdir(parents=True)
        root_home = tmp_path / "root" / ".hermes"
        root_home.mkdir(parents=True)
        unit_dir = tmp_path / "systemd"
        unit_dir.mkdir()
        (unit_dir / f"{gateway_cli._SERVICE_BASE}.service").write_text(
            f'[Service]\nEnvironment="HERMES_HOME={alice_home}"\n', encoding="utf-8"
        )
        monkeypatch.setattr(gateway_cli, "_SYSTEM_UNIT_DIR", unit_dir)
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr(hermes_constants, "_get_platform_default_hermes_home", lambda: root_home)
        monkeypatch.delenv("HERMES_HOME", raising=False)

        pre_sync_name = gateway_cli.get_service_name()
        gateway_cli._sync_hermes_home_from_systemd_unit(system=True)

        assert os.environ["HERMES_HOME"] == str(alice_home)  # the sync really ran
        assert gateway_cli.get_service_name() == pre_sync_name
