from __future__ import annotations

import re
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "optional-skills/mlops/nvidia-aiq-signal-discovery/SKILL.md"


def _read() -> tuple[dict, str]:
    content = SKILL.read_text(encoding="utf-8")
    match = re.search(r"\n---\s*\n", content[3:])
    assert match
    frontmatter = yaml.safe_load(content[3 : match.start() + 3])
    return frontmatter, content


def test_skill_has_governed_frontmatter_and_sections() -> None:
    frontmatter, content = _read()
    assert frontmatter["name"] == "nvidia-aiq-signal-discovery"
    assert len(frontmatter["description"]) <= 60
    assert frontmatter["description"].endswith(".")
    for section in (
        "## When to Use",
        "## Prerequisites",
        "## How to Run",
        "## Quick Reference",
        "## Procedure",
        "## Pitfalls",
        "## Verification",
    ):
        assert section in content


def test_skill_declares_no_send_and_native_bridge() -> None:
    _, content = _read()
    assert "scripts/bridge_nvidia_signal_result.py" in content
    assert "does not place orders" in content
    assert "research_only" in content
    assert "AIQ_SERVER_URL" in content
