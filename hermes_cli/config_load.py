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
    from hermes_cli.config_backend import supports_file_tooling
    if supports_file_tooling() and not defer_maintenance():
        from hermes_cli.config_backups import backup_config
        backup_config(config_path, "good")


def _load_config_cache_sig(config_path: Path) -> tuple[Optional[tuple[Any, ...]], Optional[tuple[Any, ...]]]:
    """Backend-aware user signature plus the applicable managed overlay and maintenance mode.

    The user signature uses the same opaque backend cache version as ``read_raw_config``;
    local-file racy digests and remote etags must never be mixed with a stat-only key.
    """
    from hermes_cli import config as cfg

    try:
        user_sig: Optional[tuple[Any, ...]] = cfg.config_cache_version(config_path)
    except FileNotFoundError:
        user_sig = None
    from hermes_cli.config_backend import get_config_backend
    from utils import file_cache_version
    managed_dir = (cfg.managed_scope.get_managed_dir()
                   if get_config_backend().honors_managed_config() else None)
    empty_managed = (0, 0, 0, 0, "")
    try:
        managed_sig = (file_cache_version(managed_dir / "config.yaml")
                       if managed_dir else empty_managed)  # config-reader: ok — managed overlay
    except OSError:
        managed_sig = empty_managed
    if user_sig is None and managed_sig == empty_managed:
        return None, None
    from hermes_cli.startup_hold import defer_maintenance
    return user_sig, (*(user_sig or empty_managed), *managed_sig, defer_maintenance())


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
            cfg.probe_config_readable(Path(path_key))
            return None
        except OSError:
            return hit
    env_snapshot = cached[len(cache_sig) + 1]
    if all(cfg._env_ref_lookup(k) == v for k, v in env_snapshot.items()):
        return hit
    return None
