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

A checkout elsewhere (``~/.cache/review-1/head``, a scratch clone outside the data root) can never
be proved deleted, so its missing checkout alone would keep the state forever. It is reclaimed
once the state has also gone unused for ``worktrees.install_state_retention_days``: every sync
recreates ``inputs/`` and every launch takes a generation lease (moving its
``.leases`` dir), so a checkout that still exists behind a mount boundary and is used at all keeps
its state. One unused for longer is rebuilt by ``hermes pm repair`` / the next activation.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _provably_deleted(root: Path, data_root: Path) -> bool:
    if root.exists():
        return False
    return root.is_relative_to(data_root) or (root.parent.name == ".worktrees" and root.parent.is_dir())


def _last_used(state: Path) -> float:
    """Newest mtime among what a sync (``inputs/`` is recreated, ``facts.json`` rewritten) or a
    launch (a generation's ``.leases`` dir) moves. Not the state dir itself: a part-way removal
    moves it, and that must not restart the clock for the retry."""
    paths = [state / "inputs", state / "facts.json"]
    for generations in (state / "environments", state / "pm-runtime" / "generations"):
        if generations.is_dir():
            paths += [generation / ".leases" for generation in generations.iterdir()]
    mtimes = [0.0]
    for path in paths:
        with contextlib.suppress(OSError):
            mtimes.append(path.lstat().st_mtime)
    return max(mtimes)


def _retention_seconds(data_root: Path) -> float | None:
    """The store-wide retention, from the config of the data root that owns ``installs/``.
    Every profile of that root shares the store, so the active profile's config must not decide
    for the others: a named profile without the key would apply the default to a state the root
    opted out of reclaiming."""
    from hermes_cli.config import cfg_get, load_config_readonly
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(data_root)
    try:
        days = cfg_get(load_config_readonly(), "worktrees", "install_state_retention_days", default=14)
    finally:
        reset_hermes_home_override(token)
    return days * 86400 if isinstance(days, (int, float)) and days > 0 else None


def _is_orphan(state: Path, data_root: Path, retention: float | None) -> bool:
    record = state / "inputs" / ".project-root"
    if state.is_symlink() or record.is_symlink() or not state.is_dir() or not record.is_file():
        return False
    try:
        root = record.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return False
    if not root or Path(root).exists():
        return False
    return _provably_deleted(Path(root), data_root) or (
        retention is not None and time.time() - _last_used(state) > retention)


def orphan_install_states(installs: Path) -> list[Path]:
    """State dirs whose recorded checkout is provably deleted, or missing and unused past the
    retention period. Dirs without a record, and dirs whose checkout exists, are left alone."""
    if not installs.is_dir():
        return []
    data_root = installs.resolve().parent
    retention = _retention_seconds(installs.parent)
    return [state for state in sorted(installs.iterdir()) if _is_orphan(state, data_root, retention)]


@contextlib.contextmanager
def _install_lock(state: Path):
    """Hold the state's install lock (``runtime_lock``'s) without waiting: yields False while
    another process holds it, or when it cannot even be opened."""
    from pm.filesystem import lock_path

    try:
        fd = lock_path(state / ".install.lock", timeout=0)
    except OSError:
        fd = None
    if fd is None:
        yield False
        return
    try:
        yield True
    finally:
        os.close(fd)


def _leased(state: Path) -> bool:
    """True while a generation lease (dependency env or PM runtime) is held."""
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


def _still_orphan(state: Path, retention: float | None) -> bool:
    return _is_orphan(state, state.parent.resolve().parent, retention)


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
    retention = _retention_seconds(installs.parent)
    for state in orphan_install_states(installs):
        try:
            with gate(state, wait=False) as guard:
                if (guard is None or not guard.qualified or guard.held()
                        or not _still_orphan(state, retention) or _leased(state)):
                    continue
                ownership = guard._snapshot()
                if _remove_state(state):
                    guard.confirm_removed(ownership)
                    removed.append(state)
        except (OSError, ValueError, LifetimeUnavailable):
            logger.debug("Retaining unqualified or inaccessible install state %s", state.name)
    return removed
