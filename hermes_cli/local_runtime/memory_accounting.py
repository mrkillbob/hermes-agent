"""Additional unified-memory use of one managed llama.cpp model child: a bound and a measurement.

Two independent answers, never merged and never substituted for each other:

* ``additional_memory_bound`` is a CEILING. llama.cpp allocates weights, KV cache and compute
  buffers when the child loads, so the only request-driven growth the engine itself limits is the
  host prompt cache (``--cache-ram``, MiB; -1 = unlimited, 0 = off). The bound is that limit as read
  from the RUNNING child (argv, then ``LLAMA_ARG_CACHE_RAM``, then the engine's own ``--help``
  default). Unreadable or unlimited -> no bound. That no other buffer grows after load is an engine
  property this module cannot prove; ``MemoryMeasurement`` is the cross-check.
* ``MemorySampler`` samples each process's kernel ``phys_footprint`` (macOS
  ``proc_pid_rusage``), the number Activity Monitor calls "Memory". It is NOT resident-set size and
  is not deduplicated across processes, so summing router + child can over-count (safe direction).
  It is sampled, so its peak is a lower bound on the true peak. Any failed sample or an unsupported
  host makes the additional figure ``None``; nothing falls back to RSS.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
import ctypes
import re
import subprocess
import sys
import threading

import psutil

from agent.memory_provider import spawn_context_thread

_MIB = 1 << 20
_RUSAGE_INFO_V2 = 2
_PHYS_FOOTPRINT_OFFSET = 72  # rusage_info_v2: uuid[16] then nine u64 fields up to ri_phys_footprint


@dataclass(frozen=True)
class MemoryBound:
    limit_bytes: int | None
    source: str   # argv | env | engine_default | unavailable
    reason: str

    @property
    def available(self) -> bool:
        return self.limit_bytes is not None


def _cache_ram_from_argv(argv: list[str]) -> int | None | bool:
    """MiB from the last ``-cram``/``--cache-ram`` in argv; False when the flag is absent."""
    found: int | None | bool = False
    for i, arg in enumerate(argv):
        name, eq, inline = arg.partition("=")
        if name in ("-cram", "--cache-ram"):
            raw = inline if eq else (argv[i + 1] if i + 1 < len(argv) else "")
            found = int(raw) if re.fullmatch(r"-?\d+", raw) else None
    return found


@lru_cache(maxsize=8)
def _engine_default_cache_ram_mib(executable: str) -> int | None:
    """The ``--cache-ram`` default the exact binary documents (same probe the supervisor uses
    for ``--load-mode``). None when the help text does not state a numeric default."""
    with suppress(Exception):
        out = subprocess.run([executable, "--help"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=15, check=False)
        match = re.search(r"--cache-ram N.*?\(default:\s*(-?\d+)", out.stdout + out.stderr, re.DOTALL)
        if match:
            return int(match.group(1))
    return None


def additional_memory_bound(argv: list[str], environ: dict[str, str] | None,
                            executable: str | None) -> MemoryBound:
    """Ceiling on request-driven growth for a child launched with ``argv``/``environ``.

    ``environ`` is None when the child's environment was unreadable: the bound is then only
    available if argv states the flag explicitly (an env override could otherwise change it)."""
    try:
        mib = _cache_ram_from_argv(argv)
    except ValueError:
        return MemoryBound(None, "unavailable", "unparseable --cache-ram in argv")
    source = "argv"
    if mib is False:
        raw = None if environ is None else environ.get("LLAMA_ARG_CACHE_RAM")
        if environ is None:
            return MemoryBound(None, "unavailable",
                               "no --cache-ram in argv and the child's environment is unreadable")
        if raw is not None:
            source = "env"
            mib = int(raw) if re.fullmatch(r"-?\d+", raw.strip()) else None
        else:
            source = "engine_default"
            mib = _engine_default_cache_ram_mib(executable) if executable else None
    if mib is None:
        return MemoryBound(None, "unavailable", "cache size not determinable")
    if mib < 0:
        return MemoryBound(None, "unavailable", "prompt cache is unlimited (-1)")
    return MemoryBound(mib * _MIB, source, f"--cache-ram {mib} MiB ({source})")


def child_bound(proc) -> MemoryBound:
    """Bound for a live psutil process (the model child)."""
    try:
        argv = proc.cmdline()
        exe = proc.exe()
    except (psutil.Error, OSError) as exc:
        return MemoryBound(None, "unavailable", f"child not inspectable: {exc}")
    environ: dict[str, str] | None
    try:
        environ = proc.environ()
    except (psutil.Error, OSError):
        environ = None
    return additional_memory_bound(argv, environ, exe)


def model_child(router, model_id: str, resident_models: list[str]):
    """The router's child serving ``model_id`` (psutil process), or None.

    Matched by the child's own ``--alias``/``--model`` argv. A sole child is accepted only when
    the caller's ``/models`` read says ``model_id`` is the one resident model: never guessed."""
    try:
        children = router.children(recursive=True)
    except (psutil.Error, OSError):
        return None
    from pathlib import PurePath

    matches = []
    for child in children:
        try:
            argv = child.cmdline()
        except (psutil.Error, OSError):
            continue
        for i, arg in enumerate(argv[:-1]):
            if (arg in ("--alias", "-a") and argv[i + 1] == model_id) or (
                    arg in ("-m", "--model") and PurePath(argv[i + 1]).stem == model_id):
                matches.append(child)
                break
    if len(matches) == 1:
        return matches[0]
    if not matches and len(children) == 1 and resident_models == [model_id]:
        return children[0]
    return None


# ── measurement ──────────────────────────────────────────────

def phys_footprint(pid: int) -> int | None:
    """Kernel-accounted physical footprint of ``pid`` in bytes (macOS), else None."""
    if sys.platform != "darwin":
        return None
    try:
        lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        buf = ctypes.create_string_buffer(512)
        rc = lib.proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(_RUSAGE_INFO_V2), buf)
        if rc != 0:
            return None
        return int.from_bytes(buf.raw[_PHYS_FOOTPRINT_OFFSET:_PHYS_FOOTPRINT_OFFSET + 8], "little")
    except (OSError, AttributeError):
        return None


@dataclass(frozen=True)
class MemoryMeasurement:
    baseline_bytes: int | None
    peak_bytes: int | None
    final_bytes: int | None
    samples: int
    failed_samples: int
    basis: str = "phys_footprint_sum"

    @property
    def complete(self) -> bool:
        return (self.baseline_bytes is not None and self.final_bytes is not None
                and self.failed_samples == 0 and self.samples >= 2)

    @property
    def additional_peak_bytes(self) -> int | None:
        """Largest observed rise over the baseline; None unless every sample succeeded."""
        if not self.complete or self.peak_bytes is None:
            return None
        return max(0, self.peak_bytes - self.baseline_bytes)

    def within(self, bound: MemoryBound) -> bool | None:
        """True/False against the ceiling; None when either side is unavailable."""
        rise = self.additional_peak_bytes
        if rise is None or bound.limit_bytes is None:
            return None
        return rise <= bound.limit_bytes


class MemorySampler:
    """Context manager: baseline on entry, background samples, final sample on exit.

    ``pids`` is called each sample (the child set can change); ``reader(pid)`` returns bytes or
    None. A None for any pid fails that whole sample: a partial sum would under-count."""

    def __init__(self, pids: Callable[[], Iterable[int]], *,
                 reader: Callable[[int], int | None] = phys_footprint, interval_s: float = 0.5):
        self._pids, self._reader, self._interval_s = pids, reader, interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._values: list[int] = []
        self._failed = 0
        self._first_ok: bool | None = None
        self._last_ok = False
        self.result: MemoryMeasurement | None = None

    def _sample(self) -> None:
        try:
            pids = list(self._pids())
            readings = [self._reader(p) for p in pids]
        except (psutil.Error, OSError, ValueError):
            pids, readings = [], []
        ok = bool(pids) and all(r is not None for r in readings)
        if ok:
            self._values.append(sum(readings))
        else:
            self._failed += 1
        if self._first_ok is None:
            self._first_ok = ok
        self._last_ok = ok

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_s):
            self._sample()

    def __enter__(self) -> MemorySampler:
        self._sample()
        self._thread = spawn_context_thread(self._loop, name="memory-sampler")
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self._sample()
        vals = self._values
        self.result = MemoryMeasurement(
            baseline_bytes=vals[0] if self._first_ok else None,
            peak_bytes=max(vals) if vals else None,
            final_bytes=vals[-1] if self._last_ok else None,
            samples=len(vals) + self._failed, failed_samples=self._failed)
