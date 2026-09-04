"""Hermes Revenue Lab plugin — in-tree.

Hermes Revenue Lab (HRL) was originally developed as a standalone repo
(``mrkillbob/HermesRevenueLab``) and integrated via an external-checkout
bridge (see git history on ``codex/revenue-lab-federation-integration``).
That standalone repo has since been folded into hermes-agent directly; HRL
now lives entirely under this directory:

- ``src/hermes_revenue_lab/`` — the HRL package itself (capital allocation,
  model/provider benchmarking, deterministic guard, compliance registry,
  cron fleet, etc). Its modules import each other as the top-level
  ``hermes_revenue_lab.*`` package (unchanged from the standalone repo), so
  this ``__init__`` adds ``src/`` to ``sys.path`` at plugin load time.
- ``scripts/`` — HRL's own operator/benchmark scripts. Several of these
  import hermes-agent internals directly (``agent.auxiliary_client``,
  ``hermes_cli.auth``, ...) — that's expected; HRL was already written
  assuming co-location with a hermes-agent checkout.
- ``tests/`` — HRL's ported test suite (``pytest plugins/hermes_revenue_lab/tests/``).
- ``docs/``, ``config/``, ``artifacts/``, ``README.md``, ``AGENTS.md``,
  ``CLAUDE.md`` — HRL's own documentation, runbooks, and prior benchmark
  artifacts, carried over unchanged.

This plugin itself adds no model tools — only an operator-facing
``hermes revenue-lab`` CLI bridge and an opt-in skill, matching the scope
the original external-checkout integration had (see PR #21 review
feedback), just pointed at in-tree code instead of an external root.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent
_HRL_SRC = _PLUGIN_ROOT / "src"

if str(_HRL_SRC) not in sys.path:
    # Insert (not append) so an in-tree HRL always wins over any
    # like-named package that might otherwise shadow it.
    sys.path.insert(0, str(_HRL_SRC))

from plugins.hermes_revenue_lab.cli import register_cli, revenue_lab_command


def register(ctx) -> None:
    skill_path = _PLUGIN_ROOT.parent.parent / "optional-skills" / "revenue" / "revenue-lab" / "SKILL.md"
    if skill_path.exists():
        ctx.register_skill(
            "revenue-lab",
            skill_path,
            "Operate the in-tree Hermes Revenue Lab plugin with its guarded runbooks.",
        )

    ctx.register_cli_command(
        name="revenue-lab",
        help="Inspect and operate the in-tree Hermes Revenue Lab plugin",
        setup_fn=register_cli,
        handler_fn=revenue_lab_command,
        description=(
            "Operator bridge for the in-tree Hermes Revenue Lab code "
            "(plugins/hermes_revenue_lab/). Reports status and runs guarded "
            "HRL scripts. Adds no model tools."
        ),
    )
