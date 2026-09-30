"""Control commands must never import an assigned PR's Hermes checkout."""

import subprocess
import os
import venv
from pathlib import Path
from tools.environments.local import build_subprocess_env

from github_pr_feedback.controller import _governed_command_prefix


def test_control_plane_command_ignores_worktree_module_shadow(tmp_path):
    home = tmp_path / "control"
    home.mkdir()
    workspace = tmp_path / "pr"
    shadow = workspace / "hermes_cli"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "main.py").write_text("print('WRONG_WORKTREE_CONTROL_PLANE')\n", encoding="utf-8")
    # Model the dispatcher-bound source via PYTHONPATH, without requiring a
    # site installation or allowing the assigned PR cwd to shadow it.
    runtime = tmp_path / "runtime"
    venv.EnvBuilder(system_site_packages=True, with_pip=False).create(runtime)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    source = Path(__file__).resolve().parents[3]
    command = _governed_command_prefix(home).removesuffix(" github-pr-feedback")
    # Exercise the actual CLI launcher without GitHub credentials or a network
    # operation; --version is sufficient to identify module resolution.
    result = subprocess.run(
        ["/bin/sh", "-c", command + " --version"], cwd=workspace,
        env=build_subprocess_env(scrub_secrets=False, inherit_profile_home=False, extra={"HERMES_KANBAN_HERMES_PYTHON": str(python), "PYTHONPATH": str(source)}),
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "WRONG_WORKTREE_CONTROL_PLANE" not in result.stdout
    assert "Hermes Agent" in result.stdout


def test_control_plane_prefix_preserves_dispatcher_source_binding(tmp_path):
    command = _governed_command_prefix(tmp_path)
    assert " -P -m hermes_cli.main " in command
    assert " -E " not in command
    assert " -I " not in command
