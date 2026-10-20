"""Prompt-cache metadata and sampled macOS process footprints.

The cache setting limits the host prompt cache only. It does not establish a
ceiling on total additional unified memory, KV/compute buffers or Metal usage.
Process coverage and birth tokens must remain fixed across each sample. Shared
footprint attribution is not deduplicated and can still distort a delta. Sampled peaks can miss spikes;
non-exceedance is inconclusive. No unsupported host falls back to RSS.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
import ctypes
import re
import math
import subprocess
import sys
import threading

import psutil

from agent.memory_provider import spawn_context_thread

_MIB = 1 << 20
_RUSAGE_INFO_V2 = 2
_PHYS_FOOTPRINT_OFFSET = (
    72  # rusage_info_v2: uuid[16] then nine u64 fields up to ri_phys_footprint
)


@dataclass(frozen=True)
class MemoryBound:
    limit_bytes: int | None
    source: str  # argv | env | engine_default | unavailable
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
        out = subprocess.run(
            [executable, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        match = re.search(
            r"--cache-ram N.*?\(default:\s*(-?\d+)", out.stdout + out.stderr, re.DOTALL
        )
        if match:
            return int(match.group(1))
    return None


def prompt_cache_limit(
    argv: list[str], environ: dict[str, str] | None, executable: str | None
) -> MemoryBound:
    """Prompt-cache limit only; never a total additional-memory ceiling.

    An unreadable environment requires an explicit argv setting.
    """
    try:
        mib = _cache_ram_from_argv(argv)
    except ValueError:
        return MemoryBound(None, "unavailable", "unparseable --cache-ram in argv")
    source = "argv"
    if mib is False:
        raw = None if environ is None else environ.get("LLAMA_ARG_CACHE_RAM")
        if environ is None:
            return MemoryBound(
                None,
                "unavailable",
                "no --cache-ram in argv and the child's environment is unreadable",
            )
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


def additional_memory_bound(
    argv: list[str], environ: dict[str, str] | None, executable: str | None
) -> MemoryBound:
    """No supported engine-wide additional-memory ceiling has been established.

    Deliberately does not execute the engine or infer a total from cache metadata.
    """
    return MemoryBound(
        None,
        "unavailable",
        "prompt-cache metadata does not bound total additional unified memory",
    )


def child_prompt_cache_limit(proc) -> MemoryBound:
    """Prompt-cache metadata for an inspected psutil process."""
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
    return prompt_cache_limit(argv, environ, exe)


def child_bound(proc) -> MemoryBound:
    """Total additional-memory ceiling remains unavailable for a live child."""
    return additional_memory_bound([], None, None)


def model_child(router, model_id: str, resident_models: list[str]):
    """The router's child serving ``model_id`` (psutil process), or None.

    Require an exact match in the child's own ``--alias``/``--model`` argv.
    Residency alone cannot identify an unreadable or contradictory descendant.
    Process-birth identity still needs verification before live integration."""
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
        aliases, models = [], []
        malformed = False
        for i, arg in enumerate(argv):
            name, eq, inline = arg.partition("=")
            if name not in ("--alias", "-a", "--model", "-m"):
                continue
            value = inline if eq else (argv[i + 1] if i + 1 < len(argv) else "")
            if not value or value.startswith("-"):
                malformed = True
            (aliases if name in ("--alias", "-a") else models).append(value)
        if malformed or len(aliases) > 1 or len(models) > 1:
            continue
        # The served alias takes precedence over the model file's basename.
        if aliases:
            matched = aliases == [model_id]
        else:
            matched = len(models) == 1 and PurePath(models[0]).stem == model_id
        if matched:
            matches.append(child)
    if len(matches) == 1:
        return matches[0]
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
        return int.from_bytes(
            buf.raw[_PHYS_FOOTPRINT_OFFSET : _PHYS_FOOTPRINT_OFFSET + 8], "little"
        )
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
        return (
            self.baseline_bytes is not None
            and self.final_bytes is not None
            and self.failed_samples == 0
            and self.samples >= 2
        )

    @property
    def additional_peak_bytes(self) -> int | None:
        """Largest observed rise over the baseline; None unless every sample succeeded."""
        if not self.complete or self.peak_bytes is None:
            return None
        return max(0, self.peak_bytes - self.baseline_bytes)

    def within(self, bound: MemoryBound) -> bool | None:
        """False for observed exceedance; otherwise inconclusive (None).

        This comparison cannot certify true-peak or process/Metal coverage.
        """
        rise = self.additional_peak_bytes
        if rise is None or bound.limit_bytes is None:
            return None
        return False if rise > bound.limit_bytes else None


def _process_birth(pid: int) -> float | None:
    """A numeric PID alone cannot identify a process across samples."""
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        return None


class MemorySampler:
    """Context manager: baseline on entry, background samples, final sample on exit.

    ``pids`` must yield the same unique process set throughout. Birth tokens are
    checked around every reading. Any gap or identity change invalidates the delta.
    This does not establish complete Metal/process coverage or deduplicate shared memory."""

    def __init__(
        self,
        pids: Callable[[], Iterable[int]],
        *,
        reader: Callable[[int], int | None] = phys_footprint,
        interval_s: float = 0.5,
        identity_reader: Callable[[int], float | None] = _process_birth,
    ):
        if not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError("sampling interval must be finite and positive")
        self._pids, self._reader, self._interval_s = pids, reader, interval_s
        self._identity_reader = identity_reader
        self._identities: tuple | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._values: list[int] = []
        self._failed = 0
        self._first_ok: bool | None = None
        self._last_ok = False
        self.result: MemoryMeasurement | None = None

    def _sample(self) -> None:
        ok = False
        try:
            pids = sorted(self._pids())
            if (
                not pids
                or len(set(pids)) != len(pids)
                or any(type(p) is not int or p <= 0 for p in pids)
            ):
                raise ValueError("process coverage is empty, duplicated or invalid")
            before = tuple((p, self._identity_reader(p)) for p in pids)
            readings = [self._reader(p) for p in pids]
            after = tuple((p, self._identity_reader(p)) for p in pids)
            identities_valid = all(
                t is not None and math.isfinite(t) and t > 0 for _, t in before
            )
            if self._identities is None and identities_valid:
                self._identities = before
            ok = (
                identities_valid
                and before == after == self._identities
                and all(type(r) is int and r >= 0 for r in readings)
            )
            if ok:
                self._values.append(sum(readings))
        except (psutil.Error, OSError, ValueError, TypeError):
            ok = False
        if not ok:
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
            samples=len(vals) + self._failed,
            failed_samples=self._failed,
        )
