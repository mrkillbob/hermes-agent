#!/usr/bin/env python3
"""Collect and publish the canonical HRL-0 environment inventory."""

import os
from pathlib import Path

from hermes_revenue_lab.inventory.collector import collect_inventory
from hermes_revenue_lab.inventory.publish import publish_inventory
from hermes_revenue_lab.inventory.types import InventoryContext
from plugins.hermes_revenue_lab.scripts.verify_isolation import verify_isolation


def main() -> int:
    # TradingBotV18 is an unrelated external project; its path is inherently
    # machine-specific — override with HRL_TRADINGBOT_ROOT if needed.
    context = InventoryContext(
        workspace=Path(__file__).resolve().parents[1],
        hermes_home=Path(__file__).resolve().parents[1] / ".hermes",
        tradingbot_path=Path(
            os.environ.get("HRL_TRADINGBOT_ROOT", str(Path.home() / "TradingBotV18"))
        ),
    )
    isolation_verdict = verify_isolation(
        context.workspace,
        context.tradingbot_path,
        context.tradingbot_path / "README.md",
    )
    inventory = collect_inventory(context, isolation_verdict=isolation_verdict)
    paths = publish_inventory(inventory, context.workspace / "artifacts" / "bootstrap")
    print(f"classification={inventory['classification']}")
    for name, path in sorted(paths.items()):
        print(f"{name}={path}")
    return 0 if not inventory.get("required_sections_blocked") else 2


if __name__ == "__main__":
    raise SystemExit(main())
