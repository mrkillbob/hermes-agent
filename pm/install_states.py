"""Reclaim per-checkout dependency state whose checkout is gone.

Dependency state lives under ``<home>/installs/<install_key>`` where the key hashes the
checkout path (:func:`pm.environments.install_key`). Every ``hermes -w`` worktree, review tree
or scratch clone that boots the agent therefore commits a full environment (venv, test venv,
PM runtime, ~200 MB each), and nothing removed it when the tree was deleted: one developer
host carried 402 such orphans (80 GB) after a fortnight of worktree campaigns.

The state dir records the checkout it belongs to in ``inputs/.project-root`` (written by
:func:`pm.environments.record_activation_inputs`). A missing path alone does not prove the
checkout is gone: a data root shared with a container (``-v ~/.hermes:/opt/data``) or an
unmounted volume records paths this process cannot see. So a dir is an orphan only when its
checkout sat where this process can see that it was deleted -- under the data root that owns
``installs/`` (scratch clones, the runtime checkout) or directly in a ``.worktrees/`` dir that
still exists. Removal still fails closed on anything a live process could hold: the install
lock, or a dependency / PM runtime generation lease.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def _provably_deleted(root: Path, data_root: Path) -> bool:
    if root.exists():
        return False
    return root.is_relative_to(data_root) or (root.parent.name == ".worktrees" and root.parent.is_dir())


def orphan_install_states(installs: Path) -> list[Path]:
    """State dirs whose recorded checkout is provably deleted. Dirs without a record are left alone."""
    if not installs.is_dir():
        return []
    data_root = installs.resolve().parent
    orphans: list[Path] = []
    for state in sorted(installs.iterdir()):
        record = state / "inputs" / ".project-root"
        if state.is_symlink() or record.is_symlink() or not state.is_dir() or not record.is_file():
            continue
        try:
            root = record.read_text(encoding="utf-8-sig").strip()
        except OSError:
            continue
        if root and _provably_deleted(Path(root), data_root):
            orphans.append(state)
    return orphans


def _held(state: Path) -> bool:
    """True while a process may still be using this state dir: the install lock is taken or a
    generation lease is held. Any lock we cannot even open counts as held."""
    from hermes_cli.runtime_state import leases_held
    from pm.filesystem import lock_fd

    locks = (state / ".install.lock", state / "test-environment" / ".install.lock",
             state / "pm-runtime" / ".prepare.lock")
    for lock in locks:
        if lock.is_symlink():
            return True
        if not lock.exists():
            continue
        try:
            fd = os.open(lock, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            return True
        try:
            if not lock_fd(fd, wait=False):
                return True
        finally:
            os.close(fd)
    for generations in (state / "environments", state / "pm-runtime" / "generations",
                        state / "test-environment"):
        if generations.is_dir():
            for generation in generations.iterdir():
                if generation.is_dir() and (generation / ".leases").is_dir() and leases_held(generation):
                    return True
    return False


def _still_orphan(state: Path) -> bool:
    record = state / "inputs" / ".project-root"
    if state.is_symlink() or record.is_symlink():
        return False
    root = record.read_text(encoding="utf-8-sig").strip()
    return bool(root) and _provably_deleted(Path(root), state.parent.resolve().parent)


def _remove_state(state: Path) -> bool:
    # Preserve the recovery record until all other children have been removed.
    for child in state.iterdir():
        if child.name == "inputs":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                child.unlink()
    if any(child.name != "inputs" for child in state.iterdir()):
        return False
    shutil.rmtree(state, ignore_errors=True)
    return not state.exists()


def collect_orphan_install_states(installs: Path) -> list[Path]:
    """Collect only identity-qualified, idle orphan domains under their outside-state gate."""
    from pm.lifetime import gate, LifetimeUnavailable

    removed: list[Path] = []
    for state in orphan_install_states(installs):
        try:
            with gate(state, wait=False) as guard:
                if (guard is None or not guard.qualified or guard.held()
                        or not _still_orphan(state) or _held(state)):
                    continue
                ownership = guard._snapshot()
                if _remove_state(state):
                    guard.confirm_removed(ownership)
                    removed.append(state)
        except (OSError, ValueError, LifetimeUnavailable):
            logger.debug("Retaining unqualified or inaccessible install state %s", state.name)
    return removed
