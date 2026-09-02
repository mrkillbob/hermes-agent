"""Hermes Revenue Lab integration plugin.

Registers operator-facing CLI and opt-in skill surfaces only. HRL remains an
external local-first project; this plugin does not add model tools.
"""

from __future__ import annotations

from pathlib import Path

from plugins.hermes_revenue_lab.cli import register_cli, revenue_lab_command


def register(ctx) -> None:
    skill_path = Path(__file__).parent / "skills" / "revenue-lab" / "SKILL.md"
    ctx.register_skill(
        "revenue-lab",
        skill_path,
        "Operate the external Hermes Revenue Lab checkout with its guarded runbooks.",
    )
    ctx.register_cli_command(
        name="revenue-lab",
        help="Inspect and operate the external Hermes Revenue Lab checkout",
        setup_fn=register_cli,
        handler_fn=revenue_lab_command,
        description=(
            "Operator bridge for the local Hermes Revenue Lab project. It reports "
            "checkout status and runs guarded HRL scripts without copying HRL into "
            "Hermes core or adding model tools."
        ),
    )
