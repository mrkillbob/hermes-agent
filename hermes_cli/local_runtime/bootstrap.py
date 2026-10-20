"""Bootstrap for the managed runtime: config -> installed binaries -> running supervised server.

``ensure_local_runtime`` requires a durable gateway owner capability. Other callers only resolve
an existing endpoint; their exit cannot tear down a router used by another process.
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
import logging
import os
import threading
import time
from pathlib import Path

from hermes_cli.local_runtime.binaries import runtimes_root
from hermes_cli.local_runtime.gguf import SPLIT_PART_RE, model_id_from_stem

logger = logging.getLogger(__name__)

_SUPERVISOR = None  # process-wide singleton; one router per Hermes process
# The engine that singleton runs (or is booting). Budgets price ITS devices: the server is shared by
# every profile this process hosts, so another profile's ``local_runtime.backend`` must not resize it.
_SERVING_ENGINE = None
_EXIT_HOOKED = False
_EXIT_CLEANUP_DELEGATED = False
_TRANSITION_LOCK = threading.RLock()
_STATE_LOCK = threading.Lock()
_BOOT_CANCEL = None
_BOOT_OWNER = None
_BOOT_GENERATION = 0
_STOPPING = 0
_SERVING_OWNER = None


def _stop_at_exit() -> None:
    """Fallback cleanup when the gateway has not already delegated its bounded stop.

    An unresponsive stop must not be re-entered synchronously during interpreter exit.
    Hard kills can leave a recorded orphan for adoption or explicit owner recovery.
    """
    global _EXIT_HOOKED
    if not _EXIT_HOOKED:
        import atexit

        atexit.register(_shutdown_at_exit)
        _EXIT_HOOKED = True


def set_exit_cleanup_delegated(delegated: bool) -> bool:
    """Transfer exit cleanup to the gateway worker; return the previous setting for rollback."""
    global _EXIT_CLEANUP_DELEGATED
    with _STATE_LOCK:
        previous = _EXIT_CLEANUP_DELEGATED
        _EXIT_CLEANUP_DELEGATED = delegated
        return previous


def _shutdown_at_exit() -> None:
    with _STATE_LOCK:
        delegated = _EXIT_CLEANUP_DELEGATED
    if not delegated:
        shutdown_local_runtime()


def _detect_gpu_vendor() -> str | None:
    """Best-effort GPU vendor for backend selection.

    Preserve NVIDIA's detailed name when nvidia-smi is available, then use the
    platform host facts so Intel and AMD do not silently demote ``auto`` to CPU.
    """
    from hermes_cli.local_runtime.hardware import _cached_nvidia_gpu_query
    from hermes_platform.host.facts import gpu_class

    query = _cached_nvidia_gpu_query()
    if query is not None and query.get("gpu_name"):
        return "nvidia " + query["gpu_name"]
    detected = gpu_class()
    return detected if detected in {"nvidia", "amd", "intel"} else None


def models_dir() -> Path:
    """Machine-scoped, deliberately NOT profile-scoped: a 20 GB GGUF is a machine asset, and every
    profile shares the one managed server that serves it (same rule as runtimes_root())."""
    from hermes_constants import get_default_hermes_root

    return get_default_hermes_root() / "models"


def assets_dir() -> Path:
    """Non-model companion files (mmproj projectors, spec-decode drafts). A subdirectory so the
    router's model listing — and staged_models() — never mistakes an asset for a servable model."""
    return models_dir() / "assets"


def staged_in(models_dir: Path, *, require_complete: bool = True) -> list[Path]:
    """Servable GGUFs in a directory: single files, plus split GGUFs once by their first part.
    With ``require_complete`` a split counts only when EVERY part is on disk — a mid-download split
    is not servable and must not surface anywhere as a model."""
    files = sorted(models_dir.glob("*.gguf"))
    names = {p.name for p in files}
    out = []
    for p in files:
        m = SPLIT_PART_RE.search(p.name)
        if m is None:
            out.append(p)
            continue
        if m.group(1) != "00001":
            continue
        stem, total = p.name[: m.start()], int(m.group(2))
        if not require_complete or all(f"{stem}-{i:05d}-of-{m.group(2)}.gguf" in names
                                       for i in range(2, total + 1)):
            out.append(p)
    return out


def adopt_legacy_models() -> list[Path]:
    """Move GGUFs left in the old per-profile ``<profile home>/models`` layout (and its assets/)
    into the machine-scoped dirs, so everything downstream keeps reading one directory.

    ``os.rename`` only: within one filesystem it is instant even for a 20 GB model, while
    ``shutil.move`` silently degrades to a copy across devices. A cross-device profile dir is left
    in place with a warning rather than copying tens of GB at session start. A name that already
    exists in the destination is left alone (check-then-rename: the only window is two processes
    adopting two profiles' same-named file at once, and a same name is the same catalog variant).
    Two processes racing on one file are harmless: the loser's rename finds the source gone and
    skips it. Returns the new paths of the moved files."""
    from hermes_constants import get_default_hermes_root, named_profile_has_identity

    profiles_root = get_default_hermes_root() / "profiles"
    if not profiles_root.is_dir():
        return []
    moved: list[Path] = []
    for home in sorted(profiles_root.iterdir()):
        old = home / "models"
        if home.name.startswith(".") or not old.is_dir() or not named_profile_has_identity(home):
            continue
        for src_dir, dest_dir in ((old, models_dir()), (old / "assets", assets_dir())):
            for src in sorted(src_dir.glob("*.gguf")):
                dest = dest_dir / src.name
                if dest.exists():
                    logger.warning("legacy model %s not moved: %s already exists", src, dest)
                    continue
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    os.rename(src, dest)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    logger.warning("legacy model %s not moved to %s: %s", src, dest_dir, exc)
                    continue
                moved.append(dest)
        for emptied in (old / "assets", old):
            with suppress(OSError):
                emptied.rmdir()
    if moved:
        logger.info("moved %d legacy model file(s) into %s", len(moved), models_dir())
    return moved


def staged_models() -> list[Path]:
    """Servable staged models (continuation parts, incomplete splits and assets/ never count)."""
    return staged_in(models_dir())


def staged_model_ids() -> list[str]:
    return [model_id_from_stem(p.stem) for p in staged_models()]


def _presets_stale() -> bool:
    """True when a staged model has no section in the preset INI — it would autoload with stock
    fit instead of a policy decision."""
    with suppress(Exception):
        from hermes_cli.local_runtime.presets import read_preset_decisions

        known = read_preset_decisions()
        return any(mid not in known or (not known[mid].refusal and not (known[mid].keys or {}).get("model"))
                   for mid in staged_model_ids())
    return False


def _incumbent_allows_boot() -> bool:
    """Never replace a live/uncertain owner, even when its router or presets need repair."""
    import psutil
    from hermes_cli.local_runtime.recovery import (
        _owner_is_dead, _same_birth, _valid_pid, is_modern, read_state,
    )
    from hermes_cli.local_runtime.supervisor import state_path

    state = read_state()
    if not state:
        return not state_path().exists()  # an unreadable record is not evidence of absence
    if is_modern(state) and not _owner_is_dead(state):
        # A previous server of this exact process may have stopped cleanly. A different live
        # owner can still respawn its child, so even a currently dead router is not replaceable.
        ours = state.get("owner_pid") == os.getpid() and _same_birth(psutil.Process(), state, "owner_")
        if not ours:
            if _presets_stale():
                logger.warning("Staged models need a refresh by the live owning gateway; deferring it")
            else:
                logger.info("Another live Hermes process owns the local runtime; adopting its endpoint")
            return False
    # Owner death is not an admission fence: adopters may still be using an orphan. Only the
    # explicit stop action may recover its tree. Even an unverified/reused live PID must not
    # cause startup to kill a process or overwrite the uncertain machine record.
    if not _valid_pid(state.get('pid')) or psutil.pid_exists(state['pid']):
        logger.info("Recorded local router is live or uncertain; leaving recovery to explicit stop")
        return False
    if os.name != "nt":
        # Even normal root termination cannot prove closure of a reparented native
        # child. Preserve modern and legacy records until qualified tree recovery.
        logger.error("Recorded local runtime tree custody is unverified; replacement refused")
        return False
    return True


def refresh_local_runtime() -> bool:
    """Stage changes for the next explicit owner restart without interrupting shared requests.

    The router has direct HTTP adopters and no admission fence: even a confirmed idle snapshot
    cannot exclude a new request before termination. Download, install and growth callers must
    defer; the existing explicit owner stop/start controls remain the disruptive action.
    """
    logger.info("Local runtime refresh deferred; explicitly stop/start the server from its owning gateway")
    return False


def _admitted_models_max(mdir: Path, configured: int, engine_tag: str | None = None) -> int:
    """Residency cap to hand the router: derived from the hardware budget, ``models_max`` as a ceiling.

    A cap of "four" on a card that holds one model is how a second child ends up paged (WDDM) and
    silently slow — llama.cpp evicts its LRU before an incoming load only when the cap says the
    card is full. A probe miss or an unpriceable model keeps the configured count: this must never
    block a boot. On Windows unified memory, free commit bounds the count too.
    """
    try:
        from hermes_cli.local_runtime.hardware import probe_budget
        from hermes_cli.local_runtime.presets import admitted_residency_count

        capacity = probe_budget(planning=True)
        live = _launch_budget(capacity) if capacity.uma else None
        cap = admitted_residency_count(mdir, capacity, configured, live=live, engine_tag=engine_tag)
    except Exception as exc:
        logger.warning("residency cap probe failed (%s); using models_max=%s", exc, configured)
        return configured
    if cap != configured:
        logger.info("residency cap: %s resident model(s) on this card (models_max=%s)",
                    cap, configured)
    return cap


def _generate_presets(mdir: Path, preset_path: Path, residents: int = 1, *,
                      engine_tag: str | None = None) -> Path | None:
    """Write the launch-policy INI for every staged model; returns the path to hand the router.

    Priced against CAPACITY, not live free VRAM: this runs while the outgoing server instance may
    still hold the card (restart, refresh after a download), and its memory is freed before the new
    instance loads anything. Pricing against live-free once pinned a fitting model's weights to CPU.

    The window is then narrowed to what fits beside other programs' GPU memory
    (``hardware.launch_budget``), which never moves weights to the CPU.

    Degradation ladder on failure: a STALE policy still beats no policy — stock fit (f16 KV at max
    context, no placement) is the silent-busy-wait failure on Windows. Keep serving with the
    previous INI when one exists; only a first boot with no INI at all falls to stock fit.
    ``residents`` is the router's model count, which splits the prompt-cache room on Windows
    unified memory.
    """
    from hermes_cli.local_runtime.hardware import probe_budget
    from hermes_cli.local_runtime.presets import generate_presets

    try:
        capacity = probe_budget(planning=True)
        for entry in generate_presets(mdir, capacity, preset_path, live=_launch_budget(capacity),
                                      residents=residents, engine_tag=engine_tag):
            if entry.refusal:
                logger.warning("model refused by physics check: %s", entry.refusal)
        return preset_path
    except Exception as exc:
        if preset_path.exists():
            logger.error("preset generation failed (%s); serving with the "
                         "PREVIOUS launch policies — models staged since "
                         "the last successful generation run unpoliced "
                         "until this is fixed", exc)
            return preset_path
        logger.error("preset generation failed (%s) and no previous "
                     "policy file exists; router runs stock fit", exc)
        return None


def _launch_budget(capacity, own_bytes: int = 0):
    """``hardware.launch_budget``, or None when the probe fails: a boot never waits on it."""
    from hermes_cli.local_runtime.hardware import launch_budget

    try:
        return launch_budget(capacity, own_bytes=own_bytes)
    except Exception as exc:
        logger.warning("free GPU memory probe failed (%s); launch windows use capacity", exc)
        return None


# Router statuses that hold GPU memory. The preset file changes only while every model is out of
# them: the router unloads a loaded model whose launch flags change on reload.
_HOLDS_MEMORY = frozenset({"loaded", "loading", "sleeping", "ready"})


def refit_idle_presets(sup) -> bool:
    """Re-plan launch windows against free GPU memory while no model is loaded.

    Boot plans every window once, but models load later (on demand, after an idle unload), and by
    then other programs may hold more or less of the card. Returns True when the router was given
    new presets. A request that starts a load between the last status check and the reload is
    unloaded by the router and fails once; that gap is a few milliseconds, and only when a window
    changes.
    """
    from hermes_cli.local_runtime.hardware import probe_budget
    from hermes_cli.local_runtime.growth import load_window_overrides
    from hermes_cli.local_runtime.presets import plan_presets, render_presets

    path = sup.preset_path
    if path is None or not path.exists() or _any_holds_memory(sup):
        return False
    capacity = probe_budget(planning=True)
    live = _launch_budget(capacity)
    if live is None:
        return False
    overrides = load_window_overrides()
    last = sup._refit_usable
    if (last is not None and not sup._refit_pending_reload and overrides == sup._refit_overrides
            and abs(live.usable_vram_bytes - last) < _REFIT_STEP_BYTES):
        return False
    text = render_presets(plan_presets(models_dir(), capacity, live=live, residents=sup.models_max,
                                       engine_tag=_SERVING_ENGINE.tag if _SERVING_ENGINE is not None else None))
    # Held across the write and the reload so a restart can't spawn between them; everything
    # slow ran above.
    with sup._lifecycle_lock:
        if sup.proc is None or sup.proc.poll() is not None or _any_holds_memory(sup):
            return False
        changed = text != path.read_text(encoding="utf-8-sig")
        needs_reload = changed or sup._refit_pending_reload
        if needs_reload:
            from utils import atomic_write_text

            # File equality after a failed reload is not proof the router applied its contents.
            sup._refit_pending_reload = True
            if changed:
                atomic_write_text(path, text, tmp_prefix=f".{path.name}_", mode=0o600)
            sup.reload_presets()
            sup._refit_pending_reload = False
        sup._refit_usable = live.usable_vram_bytes
        # Cache only applied plans, using the pre-plan snapshot so concurrent grants retry.
        sup._refit_overrides = overrides
        if not needs_reload:
            return False
    logger.info("launch windows re-planned for %.1f GiB of free GPU memory",
                live.usable_vram_bytes / (1 << 30))
    return True


# Smaller changes in free memory don't move a window by a ladder rung; skipping them keeps the
# idle loop from re-reading every model header each pass.
_REFIT_STEP_BYTES = 256 << 20


def _any_holds_memory(sup) -> bool:
    return any(status in _HOLDS_MEMORY for status in sup.models(timeout_s=5).values())


def _try_lock_boot_fd(fd: int) -> bool:
    """Non-blocking exclusive attempt; portable across fcntl/msvcrt."""
    if os.name == "nt":
        import msvcrt

        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


def _unlock_boot_fd(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _cross_process_boot_lock(timeout_s: float = 130.0, *, cancel_event=None):
    """Serialize the state-check-then-spawn sequence across every Hermes process on this
    machine — the ``_SUPERVISOR`` singleton above only rules out a race within ONE process.
    Two profiles booting in the same second each see no ``server.json`` yet and each spawn a
    router on the stable port (#116682); an OS-held lock makes the second caller wait for the
    first to publish its state file, so it re-checks and adopts instead of spawning a duplicate.
    Failure grants no boot authority. Consumers can still adopt a verified endpoint, but an
    unwritable lock or contention timeout must never create an unlocked second router."""
    path = runtimes_root() / "boot.lock"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        logger.warning("boot lock unavailable (%s); refusing to boot", exc)
        yield False
        return
    try:
        deadline = time.monotonic() + timeout_s
        acquired = _try_lock_boot_fd(fd)
        while not acquired:
            if cancel_event is not None and cancel_event.is_set():
                yield False
                return
            if time.monotonic() >= deadline:
                logger.warning("boot lock contended past %.0fs; refusing to boot", timeout_s)
                yield False
                return
            if cancel_event is None:
                time.sleep(0.2)
            else:
                cancel_event.wait(0.2)
            acquired = _try_lock_boot_fd(fd)
        try:
            yield True
        finally:
            with suppress(OSError):
                _unlock_boot_fd(fd)
    finally:
        os.close(fd)


def ensure_local_runtime(config: dict, force: bool = False, *, owner=None,
                         boot_generation: int | None = None) -> object | None:
    """Only a durable owner may boot; consumers resolve/adopt through endpoint.py instead.

    ``force`` is an explicit server action, not permission for a consumer to become owner.
    Failures keep the configured provider's existing unavailable/fallback behavior.
    """
    global _BOOT_CANCEL, _BOOT_OWNER, _EXIT_CLEANUP_DELEGATED
    if boot_generation is None:
        boot_generation = current_boot_generation()
    section = (config or {}).get("local_runtime") or {}
    if owner is None or owner.stopping.is_set():
        return None
    if not force and not section.get("enabled"):
        return None
    with owner.observing_boot(), _TRANSITION_LOCK:
        with _STATE_LOCK:
            if _STOPPING or owner.stopping.is_set() or boot_generation != _BOOT_GENERATION:
                return None
            _EXIT_CLEANUP_DELEGATED = False
            cancel = threading.Event()
            _BOOT_CANCEL = cancel
            _BOOT_OWNER = owner
        try:
            if _SUPERVISOR is not None:
                return _SUPERVISOR
            return _boot_owned_runtime(section, owner, cancel, force=force)
        except Exception as exc:
            logger.warning("managed local runtime unavailable: %s", exc, exc_info=True)
            return None
        finally:
            with _STATE_LOCK:
                if _BOOT_CANCEL is cancel:
                    _BOOT_CANCEL = None
                    _BOOT_OWNER = None


def _boot_owned_runtime(section: dict, owner, cancel, *, force: bool):
    global _SUPERVISOR, _SERVING_ENGINE, _SERVING_OWNER

    with _cross_process_boot_lock(cancel_event=cancel) as acquired:
        if not acquired or cancel.is_set() or owner.stopping.is_set():
            return None
        if not _incumbent_allows_boot():
            return None
        try:
            adopt_legacy_models()
        except OSError as exc:  # an unreadable profiles dir must not block serving what's staged
            logger.warning("legacy model adoption failed: %s", exc)
        if not force and not staged_models():
            logger.info("local runtime enabled but no models staged; not booting")
            return None

        try:
            from hermes_cli.local_runtime.binaries import installed_engine
            from hermes_cli.local_runtime.supervisor import LlamaServerSupervisor

            engine = installed_engine(section.get("backend", "auto"))
            if engine is None:
                logger.info("local runtime enabled but no PM engine installed; use the Local Models pane")
                return None
            _SERVING_ENGINE = engine

            mdir = models_dir()
            mdir.mkdir(parents=True, exist_ok=True)
            models_max = _admitted_models_max(mdir, int(section.get("models_max", 4)), engine.tag)
            preset_path = _generate_presets(mdir, runtimes_root() / "presets.ini", models_max,
                                            engine_tag=engine.tag)
            if cancel.is_set() or owner.stopping.is_set():
                _SERVING_ENGINE = None
                return None
            sup = LlamaServerSupervisor(engine.binary, mdir, preset_path=preset_path,
                                        models_max=models_max,
                                        port=int(section.get("port", 0)) or None)
            try:
                sup.start(cancel_event=cancel)
                if cancel.is_set() or owner.stopping.is_set():
                    sup.stop()
                    _SERVING_ENGINE = None
                    return None
            except Exception:
                # start() can fail after the router process exists (health timeout): leaving it
                # running unsupervised strands its VRAM behind a port nothing will clean up.
                with suppress(Exception):
                    sup.stop()
                raise
            _SUPERVISOR = sup
            _SERVING_OWNER = owner
            _stop_at_exit()
            logger.info("managed llama-server up at %s (backend=%s tag=%s)", sup.base_url, engine.backend, engine.tag)
            _start_idle_sweeper(sup)
            return sup
        except Exception as exc:
            _SERVING_ENGINE = None
            logger.warning("managed local runtime unavailable: %s", exc, exc_info=True)
            return None


def shutdown_local_runtime(*, on_stopped=None) -> None:
    """Cancel a pending boot before waiting for its transition, then stop only our supervisor."""
    global _SUPERVISOR, _SERVING_ENGINE, _SERVING_OWNER, _STOPPING, _BOOT_GENERATION
    with _STATE_LOCK:
        _BOOT_GENERATION += 1
        _STOPPING += 1
    cancel_pending_boot()
    try:
        with _TRANSITION_LOCK:
            stopped_supervisor = _SUPERVISOR is not None
            if _SUPERVISOR is not None:
                _SUPERVISOR.stop()
                _SUPERVISOR = None
            _SERVING_ENGINE = None
            _SERVING_OWNER = None
            if on_stopped is not None:
                on_stopped(stopped_supervisor)
    finally:
        with _STATE_LOCK:
            _BOOT_GENERATION += 1
            _STOPPING -= 1


def cancel_pending_boot(owner=None) -> None:
    with _STATE_LOCK:
        cancel = _BOOT_CANCEL if owner is None or _BOOT_OWNER is owner else None
    if cancel is not None:
        cancel.set()


def current_boot_generation() -> int:
    """Stop invalidates queued owner callbacks as well as already admitted boots."""
    with _STATE_LOCK:
        return _BOOT_GENERATION


@contextmanager
def runtime_control_scope(generation: int, *, blocking: bool = True):
    """Order mutations with Stop; HTTP job admission rejects an occupied transition."""
    if not _TRANSITION_LOCK.acquire(blocking=blocking):
        raise RuntimeError("Local runtime control is busy; retry after the current action completes")
    try:
        with _STATE_LOCK:
            if _STOPPING or generation != _BOOT_GENERATION:
                raise RuntimeError("Local runtime control changed; retry after the current action completes")
        yield
    finally:
        _TRANSITION_LOCK.release()


def has_owned_runtime() -> bool:
    # Publication precedes clearing the pending bit under this lock: cleanup cannot observe
    # an old "no supervisor" followed by a new "no pending boot" and miss the live server.
    with _STATE_LOCK:
        return _SUPERVISOR is not None or _BOOT_CANCEL is not None


def get_supervisor():
    """The process-local supervisor, or None (a server may still run under another process —
    check the state file)."""
    return _SUPERVISOR


def serving_engine():
    """The engine the process-local server runs or is booting, or None when there is none."""
    return _SERVING_ENGINE


_SWEEP_EVERY_S = 120
_REFIT_EVERY_S = 30


def _start_idle_sweeper(sup) -> None:
    """Idle-residency loop: every couple of minutes, unload models idle past the supervisor's
    threshold; every 30 s with nothing loaded, re-plan launch windows against free GPU memory.
    Daemon thread tied to the supervisor's lifetime — exits when the server stops."""
    import threading

    def _loop():
        last_sweep = time.monotonic()
        while sup.proc is not None and sup.proc.poll() is None:
            time.sleep(_REFIT_EVERY_S)
            if time.monotonic() - last_sweep >= _SWEEP_EVERY_S:
                last_sweep = time.monotonic()
                try:
                    sup.sweep_idle()
                except Exception as exc:
                    logger.debug("idle sweep skipped: %s", exc)
            try:
                refit_idle_presets(sup)
            except Exception as exc:
                logger.debug("launch window re-plan skipped: %s", exc)

    threading.Thread(target=_loop, daemon=True, name="local-runtime-idle-sweep").start()
