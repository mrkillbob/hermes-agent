"""Config-read cache policy and deferred home/snapshot maintenance."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def _initialize_home_for_read():
    from hermes_cli import config as cfg
    from hermes_cli.startup_hold import defer_maintenance
    if defer_maintenance():
        from hermes_constants import assert_named_profile_home_live
        assert_named_profile_home_live(cfg.get_hermes_home())
    else:
        cfg.ensure_hermes_home()


def record_good_config(config_path: Path):
    """Only ordinary successful reads create, migrate or prune config snapshots."""
    from hermes_cli.startup_hold import defer_maintenance
    if not defer_maintenance():
        from hermes_cli.config_backups import backup_config
        backup_config(config_path, "good")


def _load_config_cache_sig(config_path: Path) -> tuple[Optional[tuple[int, int, int, int]], Optional[tuple[int, ...]]]:
    """Return ``(user_sig, cache_sig)`` for ``cfg._LOAD_CONFIG_CACHE``.
    The managed signature and maintenance mode are folded in so either change invalidates
    the merged result. ``cache_sig`` is None only when neither file exists (nothing to cache on)."""
    from hermes_cli import config as cfg

    try:
        st = config_path.stat()
        user_sig: Optional[tuple[int, int, int, int]] = cfg.file_signature(st)
    except FileNotFoundError:
        user_sig = None
    managed_dir = cfg.managed_scope.get_managed_dir()
    try:
        mst = (managed_dir / "config.yaml").stat() if managed_dir else None
        managed_sig = cfg.file_signature(mst) if mst else (0, 0, 0, 0)
    except OSError:
        managed_sig = (0, 0, 0, 0)
    if user_sig is None and managed_sig == (0, 0, 0, 0):
        return None, None
    from hermes_cli.startup_hold import defer_maintenance
    return user_sig, (*(user_sig or (0, 0, 0, 0)), *managed_sig, defer_maintenance())


def _load_config_cache_hit(path_key: str, cache_sig: Any) -> Optional[dict[str, Any]]:
    """Lookup: the cached expanded config for ``path_key`` if its signature equals
    ``cache_sig`` AND every ``${VAR}`` it was expanded against still has the same value, else
    ``None``. Signatures matching is not enough: a load before load_hermes_dotenv() would otherwise
    pin unexpanded literals (e.g. auxiliary.<task>.api_key) for the process lifetime (#58514).
    Shared by the fast path and locked re-check in ``config._load_config_impl``."""
    from hermes_cli import config as cfg

    cached = cfg._LOAD_CONFIG_CACHE.get(path_key)
    if cached is None or cache_sig is None or cached[:len(cache_sig)] != cache_sig:
        return None
    hit = cached[len(cache_sig)]
    if isinstance(hit, cfg.FailedConfigRead) and isinstance(hit.read_error, OSError):
        # A read error (EMFILE/EIO/sharing violation) can clear without touching the file's
        # signature: serve the fallback only while the file still cannot be read.
        try:
            with open(path_key, "rb") as f:
                f.read()
            return None
        except OSError:
            return hit
    env_snapshot = cached[len(cache_sig) + 1]
    if all(cfg._env_ref_lookup(k) == v for k, v in env_snapshot.items()):
        return hit
    return None
