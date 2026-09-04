"""Deterministic JSON and Markdown projections for HRL-0."""

import json
from collections.abc import Mapping


def render_json(inventory: Mapping[str, object]) -> str:
    return json.dumps(inventory, sort_keys=True, indent=2) + "\n"


def render_markdown(inventory: Mapping[str, object]) -> str:
    unknowns = list(inventory.get("unknowns", []))
    warnings = list(inventory.get("warnings", []))
    lines = [
        "# Hermes Revenue Lab Environment Inventory",
        "",
        f"- Inventory ID: `{inventory['inventory_id']}`",
        f"- Classification: `{inventory['classification']}`",
        f"- Collected at: `{inventory['collected_at']}`",
        "",
        "## Unknowns",
        "",
    ]
    lines.extend(f"- {item}" for item in unknowns)
    if not unknowns:
        lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in warnings)
    if not warnings:
        lines.append("- None")
    lines.extend(["", "## Machine-readable detail", "", "See `environment_inventory.json`.", ""])
    return "\n".join(lines)
