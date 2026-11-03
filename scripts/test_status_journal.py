"""Bounded body-free per-file runner checkpoints, written before cleanup.

Explicit opt-in only. No stdout, argv, exception text, environment or fixture data.
Writes are best effort and must never prevent the runner's owned-child cleanup.
"""
from __future__ import annotations

import atexit
import json
import math
import os
from pathlib import Path
import re
import threading

_PHASES = frozenset(('started', 'output_collected', 'timed_out', 'interrupted',
                    'scratch_cleanup_started', 'scratch_cleanup_complete'))
_MAX_RECORDS = 32768
_MAX_BYTES = 8 * 1024 * 1024


class StatusJournal:
    def __init__(self, path: Path):
        # A caller must choose a fresh, existing parent outside disposable test
        # roots. Never truncate a prior receipt or follow a destination symlink.
        self._fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        self._lock = threading.Lock()
        self._records = self._bytes = self._attempt = 0
        self._disabled = False
        atexit.register(self.close)

    def close(self):
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def start(self, file: Path, repo_root: Path):
        try:
            name = file.resolve().relative_to(repo_root.resolve()).as_posix()
        except (OSError, ValueError):
            name = None
        if name is not None and (len(name) > 512 or not re.fullmatch(r'tests/[A-Za-z0-9_./-]+\.py', name)):
            name = None
        with self._lock:
            self._attempt += 1
            attempt = self._attempt
        self.emit(attempt, name, 'started', 0.0)
        return attempt, name

    def emit(self, attempt, file, phase, elapsed, exit_code=None):
        if (type(attempt) is not int or not 0 < attempt < 2**31
                or phase not in _PHASES or not isinstance(elapsed, (int, float))
                or not math.isfinite(elapsed) or elapsed < 0):
            return
        if exit_code is not None and (type(exit_code) is not int or not -(2**31) <= exit_code < 2**32):
            return
        if file is not None and (not isinstance(file, str) or len(file) > 512
                or not re.fullmatch(r'tests/[A-Za-z0-9_./-]+\.py', file)
                or '..' in file.split('/')):
            return
        row = dict(schema=1, attempt=attempt, file=file, phase=phase,
                   elapsed_ms=round(min(elapsed, (2**53 - 1) / 1000) * 1000),
                   observed_exit_code=exit_code, runner_exit_code=124 if phase == 'timed_out' else None)
        data = (json.dumps(row, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')
        with self._lock:
            if self._disabled or self._fd is None:
                return
            if self._records >= _MAX_RECORDS or self._bytes + len(data) > _MAX_BYTES:
                self._disabled = True
                _warn('WARNING: test status journal reached its bounded size limit')
                return
            try:
                # Each checkpoint reaches the OS before teardown, without a
                # buffered Python stream waiting for suite finalization.
                if os.write(self._fd, data) != len(data):
                    raise OSError('short journal write')
            except OSError:
                self._disabled = True
                _warn('WARNING: test status journal unavailable')
                return
            self._records += 1
            self._bytes += len(data)


def _warn(message):
    try:
        print(message, flush=True)
    except (OSError, ValueError):
        pass  # Even a closed diagnostic stream must not interrupt child cleanup.
