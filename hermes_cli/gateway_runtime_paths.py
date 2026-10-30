"""Committed PM environment paths for gateway services."""

from __future__ import annotations

from pathlib import Path

def _pm_runtime_venv_dir(project_root: Path | None = None) -> Path | None:
    """The venv pm provisioned for this install, resolved through
    ``runtime_paths.selected_venv`` — the committed-environment contract —
    never from interpreter state.

    Under no-boot-through-venv the gateway runs the store python with the
    venv's site-packages on PYTHONPATH, so ``sys.prefix`` always equals
    ``sys.base_prefix`` and ``VIRTUAL_ENV`` is unset in bundled installs;
    prefix/env probing silently degrades. Resolution follows the committed-
    environment contract: a committed selection is returned as-is, a host
    with nothing committed yields nothing launchable, and a malformed
    selection raises — fail closed, never a silently wrong venv.
    """
    from hermes_cli.gateway import PROJECT_ROOT, Path
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    from pm.environments import selected_venv

    venv = selected_venv(root)  # a malformed committed selection raises: fail closed
    return venv if venv.is_dir() else None
