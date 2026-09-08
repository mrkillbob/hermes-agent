"""Private, content-addressed CI diagnostics; never included in GitHub comments."""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path

from hermes_constants import get_hermes_home

_DIAGNOSTIC_TTL_SECONDS = 14 * 24 * 60 * 60
_MAX_DIAGNOSTIC_FILES = 100
_MAX_DIAGNOSTIC_BYTES = 50 * 1024 * 1024


def _output_root() -> Path:
    return get_hermes_home() / 'github-pr-feedback' / 'ci-output'


def cleanup_outputs() -> None:
    """Best-effort removal of stale or excess diagnostic artifacts."""
    root = _output_root()
    try:
        entries = [
            path for path in root.iterdir()
            if path.is_file() and not path.is_symlink() and path.suffix == '.log'
        ]
    except OSError:
        return

    cutoff = time.time() - _DIAGNOSTIC_TTL_SECONDS
    retained: list[tuple[Path, int, int]] = []
    for path in entries:
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        retained.append((path, stat.st_size, stat.st_mtime_ns))

    retained.sort(key=lambda item: item[2], reverse=True)
    total_bytes = 0
    for index, (path, size, _mtime_ns) in enumerate(retained):
        if index >= _MAX_DIAGNOSTIC_FILES or total_bytes + size > _MAX_DIAGNOSTIC_BYTES:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        total_bytes += size


def retain_output(output: str) -> Path | None:
    if not output:
        return None
    payload = output.encode('utf-8', errors='replace')
    digest = hashlib.sha256(payload).hexdigest()
    root = _output_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = root / (digest + '.log')
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=root)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_symlink() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise RuntimeError('CI diagnostic artifact identity mismatch')
    finally:
        Path(temporary).unlink()
    cleanup_outputs()
    return destination
