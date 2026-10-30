"""Seeded discovery evidence must outlive the system temp purge while its card waits in ready."""
from __future__ import annotations

from pathlib import Path

from scripts.federation_discovery import new_evidence_workspace


def test_evidence_workspace_is_under_the_board_workspaces_root_not_system_temp(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from hermes_cli import kanban_db

    workspace = Path(new_evidence_workspace({"id": "arts_media", "board": "default"}))
    assert workspace.is_dir() and workspace.name.startswith("hermes-discovery-arts_media-")
    assert workspace.parent == kanban_db.workspaces_root(board="default")
