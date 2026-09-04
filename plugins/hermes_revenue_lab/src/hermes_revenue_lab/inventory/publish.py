"""Validated staged publication for canonical HRL-0 artifacts."""

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
import uuid

from .redaction import PublicationSafetyError, assert_publication_safe
from .render import render_json, render_markdown

_PAYLOAD_NAMES = (
    "environment_inventory.json",
    "environment_inventory.md",
    "command_manifest.json",
    "desktop_connection_verdict.json",
)


def _atomic_write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, path)


def _checksum_text(directory: Path) -> str:
    lines = []
    for name in _PAYLOAD_NAMES:
        digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
    return "\n".join(lines) + "\n"


def publish_inventory(
    inventory: Mapping[str, object],
    artifact_root: Path,
) -> Mapping[str, Path]:
    """Validate candidates, retain run evidence, then replace canonical files."""

    inventory_id = str(inventory["inventory_id"])
    run_dir = artifact_root / "runs" / inventory_id
    run_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    run_dir.chmod(0o700)
    try:
        assert_publication_safe(inventory)
    except PublicationSafetyError as error:
        _atomic_write(
            run_dir / "rejection.json",
            json.dumps(
                {"inventory_id": inventory_id, "status": "rejected", "reason": str(error)},
                sort_keys=True,
                indent=2,
            )
            + "\n",
            0o600,
        )
        raise

    command_manifest = {
        "inventory_id": inventory_id,
        "commands": inventory.get("source_commands", []),
    }
    desktop_verdict = {
        "inventory_id": inventory_id,
        "status": "not_observed",
        "gateway_name": "Hermes Revenue Lab",
        "endpoint": "http://127.0.0.1:9120",
    }
    candidates = {
        "environment_inventory.json": render_json(inventory),
        "environment_inventory.md": render_markdown(inventory),
        "command_manifest.json": json.dumps(command_manifest, sort_keys=True, indent=2) + "\n",
        "desktop_connection_verdict.json": json.dumps(desktop_verdict, sort_keys=True, indent=2) + "\n",
    }
    for value in (command_manifest, desktop_verdict):
        assert_publication_safe(value)
    for name, text in candidates.items():
        _atomic_write(run_dir / name, text)
    json.loads((run_dir / "environment_inventory.json").read_text(encoding="utf-8"))
    _atomic_write(run_dir / "inventory_checksums.sha256", _checksum_text(run_dir))

    artifact_root.mkdir(parents=True, exist_ok=True)
    for name in (*_PAYLOAD_NAMES, "inventory_checksums.sha256"):
        _atomic_write(artifact_root / name, (run_dir / name).read_text(encoding="utf-8"))
    return {
        "environment_inventory_json": artifact_root / "environment_inventory.json",
        "environment_inventory_md": artifact_root / "environment_inventory.md",
        "command_manifest_json": artifact_root / "command_manifest.json",
        "desktop_connection_verdict_json": artifact_root / "desktop_connection_verdict.json",
        "inventory_checksums": artifact_root / "inventory_checksums.sha256",
        "run_directory": run_dir,
    }


def update_desktop_verdict(
    artifact_root: Path,
    verdict: Mapping[str, object],
) -> Path:
    """Bind a secret-free Desktop result to the current inventory and refresh checksums."""

    inventory_path = artifact_root / "environment_inventory.json"
    if not inventory_path.is_file():
        raise FileNotFoundError("canonical environment inventory must exist before Desktop smoke")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory_id = str(inventory["inventory_id"])
    if verdict.get("gateway_name") != "Hermes Revenue Lab":
        raise ValueError("Desktop gateway name must be Hermes Revenue Lab")
    if verdict.get("endpoint") != "http://127.0.0.1:9120":
        raise ValueError("Desktop endpoint must be http://127.0.0.1:9120")

    existing_path = artifact_root / "desktop_connection_verdict.json"
    existing: dict[str, object] = {}
    if existing_path.is_file():
        candidate_existing = json.loads(existing_path.read_text(encoding="utf-8"))
        if str(candidate_existing.get("inventory_id")) == inventory_id:
            existing = dict(candidate_existing)
    bound_verdict = {**existing, **dict(verdict), "inventory_id": inventory_id}
    assert_publication_safe(bound_verdict)
    payload = json.dumps(bound_verdict, sort_keys=True, indent=2) + "\n"
    run_dir = artifact_root / "runs" / inventory_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"inventory run directory is missing: {inventory_id}")

    _atomic_write(run_dir / "desktop_connection_verdict.json", payload)
    _atomic_write(run_dir / "inventory_checksums.sha256", _checksum_text(run_dir))
    _atomic_write(artifact_root / "desktop_connection_verdict.json", payload)
    _atomic_write(artifact_root / "inventory_checksums.sha256", _checksum_text(artifact_root))
    return artifact_root / "desktop_connection_verdict.json"
