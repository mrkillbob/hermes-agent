"""In-session context growth for the managed llama.cpp runtime.

Consumers may record deferred grants for the identity-verified managed router.
Only its durable owner applies presets; detected external servers keep their own policies.
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)
_GRANT_WRITER_LOCK = threading.Lock()
_GRANT_LOCK_HOLDER = threading.local()


def window_overrides_path():
    from hermes_cli.local_runtime.binaries import runtimes_root

    return runtimes_root() / "window_overrides.json"


def load_window_overrides(*, strict: bool = False) -> dict:
    """model_id -> granted window. Readers tolerate damage; writers must preserve it."""
    try:
        with open(window_overrides_path(), encoding="utf-8-sig") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("Invalid pending window grants")
        return {str(k): int(v) for k, v in data.items()}
    except FileNotFoundError:
        return {}
    except Exception:
        if strict:
            raise
        return {}


def _write_overrides(overrides: dict) -> None:
    from utils import mkstemp_beside

    path = window_overrides_path().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = mkstemp_beside(path, prefix=".window-grants_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(overrides, stream, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
        # No copy/in-place fallback: readers must see an entire old or new grant map.
    finally:
        with suppress(OSError):
            os.unlink(temporary)


@contextmanager
def _overrides_lock():
    from hermes_cli import auth

    if auth.fcntl is None and auth.msvcrt is None:
        raise OSError("No native pending-grant lock is available")
    if not _GRANT_WRITER_LOCK.acquire(timeout=2):
        raise TimeoutError("Pending-grant writer is busy")
    try:
        with auth._file_lock(window_overrides_path().with_suffix(".lock"),
                             _GRANT_LOCK_HOLDER, 2, "Pending-grant writer is busy"):
            yield
    finally:
        _GRANT_WRITER_LOCK.release()


def save_window_override(model_id: str, window: int, *, only_grow: bool = False) -> None:
    """Serialize pending grants; stale growth requests cannot shrink a newer request."""
    with _overrides_lock():
        overrides = load_window_overrides(strict=True)
        overrides[model_id] = (max(int(window), overrides.get(model_id, 0))
                               if only_grow else int(window))
        _write_overrides(overrides)


def clear_window_override(model_id: str) -> None:
    """Drop a model's growth state (delete/re-download paths)."""
    with _overrides_lock():
        overrides = load_window_overrides(strict=True)
        if model_id in overrides:
            del overrides[model_id]
            _write_overrides(overrides)


def is_managed_endpoint(base_url: str) -> bool:
    """True when base_url is the server this process's state file points at."""
    with suppress(Exception):
        from hermes_cli.local_runtime.endpoint import _state_endpoint

        state = _state_endpoint()
        return state is not None and (
            (base_url or "").rstrip("/") == str(state.get("base_url", "")).rstrip("/"))
    return False


def maybe_grow_window(model_id: str, *, base_url: str, session_tokens: int,
                      current_window: int,
                      measured_decode_tok_s: float | None = None) -> int | None:
    """One growth evaluation + execution. Returns the NEW window when the ladder granted a bigger
    one, else None (hold / compress / not ours).

    The caller's request boundary does not fence other processes' requests. A saved grant may
    remain pending until an eligible idle replan or explicit owner restart; compression proceeds meanwhile.
    """
    from hermes_cli.local_runtime.bootstrap import (
        _launch_budget, get_supervisor, refresh_local_runtime, staged_models)
    from hermes_cli.local_runtime.context_policy import growth_decision
    from hermes_cli.local_runtime.estimator import as_loaded, profile_from_gguf
    from hermes_cli.local_runtime.gguf import model_id_from_stem, read_gguf_header
    from hermes_cli.local_runtime.hardware import engine_scope, probe_budget
    from hermes_cli.local_runtime.presets import (
        preset_for_model, read_preset_decisions, resident_footprint)

    sup = get_supervisor()
    if not is_managed_endpoint(base_url):
        return None
    engine = None
    if sup is None:
        from hermes_cli.local_runtime.endpoint import managed_engine

        engine = managed_engine(base_url)
        if engine is None:
            return None  # Never price an adopted server using the consumer's backend.

    gguf = next((p for p in staged_models() if model_id_from_stem(p.stem) == model_id), None)
    if gguf is None:
        return None

    try:
        profile = profile_from_gguf(read_gguf_header(gguf))
    except (ValueError, OSError) as exc:
        logger.debug("growth skip %s: unreadable gguf (%s)", model_id, exc)
        return None

    try:
        if sup is not None:
            server_idle = sup.is_idle(model_id)
        else:
            from hermes_cli.local_runtime.endpoint import managed_model_idle

            server_idle = managed_model_idle(base_url, model_id)
    except Exception:
        server_idle = False

    with engine_scope(engine):
        budget = probe_budget(planning=True)
        decision = growth_decision(
            # Capacity budget: a pending grant applies after the current model exits.
            as_loaded(profile, budget), budget,
            current_window=current_window,
            session_tokens=session_tokens,
            measured_decode_tok_s=measured_decode_tok_s,
            server_idle=server_idle,
            # The caller's compression gate already confirmed its own occupancy.
            occupancy_confirmed=True,
        )
        if decision.action != "grow" or not decision.next_window:
            logger.debug("growth %s: %s (%s)", model_id, decision.action, decision.reason)
            return None

        # The growing model's residency becomes available at its eventual reload.
        live = _launch_budget(budget, own_bytes=resident_footprint(gguf, budget, current_window) or 0)
        plan = preset_for_model(gguf, budget, set(), requested_window=decision.next_window, live=live)
    if plan is None or plan.refusal or plan.window < decision.next_window:
        logger.debug("growth %s: the next rung does not fit beside other programs' GPU memory",
                     model_id)
        return None

    logger.info("context growth %s: %s", model_id, decision.reason)
    save_window_override(model_id, decision.next_window, only_grow=True)
    if sup is None:
        # An adopting worker can request a future preset, never reload another process's router.
        logger.info("growth %s: consumer saved a deferred grant; compression proceeds", model_id)
        return None
    if not refresh_local_runtime():
        # The override remains available to future preset planning; report no growth NOW so the caller
        # compresses instead of overflowing a stale window.
        logger.info("growth %s: saved for an eligible idle replan or explicit restart; compression proceeds", model_id)
        return None
    materialized = read_preset_decisions().get(model_id)
    if materialized is None or materialized.window < decision.next_window:
        logger.warning("growth %s: refreshed preset did not grant the requested window", model_id)
        return None
    return materialized.window
