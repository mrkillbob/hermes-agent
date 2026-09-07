from __future__ import annotations

import re
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "optional-skills/mlops/nvidia-aiq-signal-discovery/SKILL.md"


def _parse_frontmatter(frontmatter: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, parsed)]
    pending_key: str | None = None
    pending_parent: dict[str, Any] | None = None
    for raw_line in frontmatter.splitlines():
        if not raw_line.strip():
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            assert pending_key is not None
            assert pending_parent is not None
            if not isinstance(pending_parent.get(pending_key), list):
                pending_parent[pending_key] = []
            item: dict[str, Any] = {}
            pending_parent[pending_key].append(item)
            stack.append((indent, item))
            line = line[2:]
            parent = item
        key, _, value = line.partition(":")
        if not value.strip():
            child: dict[str, Any] = {}
            assert isinstance(parent, dict)
            parent[key] = child
            pending_key = key
            pending_parent = parent
            stack.append((indent, child))
            continue
        assert isinstance(parent, dict)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            parent[key] = [part.strip() for part in value[1:-1].split(",")]
        else:
            parent[key] = value
        pending_key = key
        pending_parent = parent
    return parsed


def _read() -> tuple[dict[str, Any], str]:
    content = SKILL.read_text(encoding="utf-8")
    match = re.search(r"\n---\s*\n", content[3:])
    assert match
    frontmatter = _parse_frontmatter(content[3 : match.start() + 3])
    return frontmatter, content


def test_skill_has_governed_frontmatter_and_sections() -> None:
    frontmatter, content = _read()
    assert frontmatter["name"] == "nvidia-aiq-signal-discovery"
    assert len(frontmatter["description"]) <= 60
    assert frontmatter["description"].endswith(".")
    metadata = frontmatter["metadata"]
    assert isinstance(metadata, dict)
    hermes = metadata["hermes"]
    assert isinstance(hermes, dict)
    config = hermes["config"]
    assert isinstance(config, list)
    assert config[0]["key"] == "AIQ_SERVER_URL"
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
