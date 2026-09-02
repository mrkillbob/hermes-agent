from __future__ import annotations

import json
from argparse import Namespace

from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from plugins.hermes_revenue_lab import register
from plugins.hermes_revenue_lab.cli import revenue_lab_command, status_payload


def test_register_adds_cli_and_skill() -> None:
    manager = PluginManager()
    manifest = PluginManifest(name="hermes_revenue_lab")
    ctx = PluginContext(manifest, manager)

    register(ctx)

    assert "revenue-lab" in manager._cli_commands
    assert manager._cli_commands["revenue-lab"]["plugin"] == "hermes_revenue_lab"
    assert manager.find_plugin_skill("hermes_revenue_lab:revenue-lab") is not None


def test_status_payload_validates_external_checkout(tmp_path) -> None:
    root = tmp_path / "HermesRevenueLab"
    (root / "docs" / "runbooks").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = \"hermes-revenue-lab\"\nversion = \"0.1.0\"\n",
        encoding="utf-8",
    )
    (root / "docs" / "runbooks" / "hrl-0.md").write_text("# HRL-0\n", encoding="utf-8")

    payload = status_payload(root)

    assert payload["status"] == "available"
    assert payload["root"] == str(root)
    assert payload["runbook_count"] == 1
    assert payload["entrypoints"]["guard"].endswith("scripts/revenue_guard.py")


def test_missing_checkout_returns_unavailable_json(capsys, tmp_path) -> None:
    rc = revenue_lab_command(
        Namespace(
            func=None,
            revenue_lab_action="status",
            root=str(tmp_path / "missing"),
        )
    )

    assert rc == 2

    from plugins.hermes_revenue_lab.cli import _cmd_status

    rc = revenue_lab_command(
        Namespace(
            func=_cmd_status,
            revenue_lab_action="status",
            root=str(tmp_path / "missing"),
        )
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["status"] == "unavailable"
