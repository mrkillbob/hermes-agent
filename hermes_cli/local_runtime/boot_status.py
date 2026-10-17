"""Read-only evidence of a durable owner's queued or active boot; never boot authority."""
from __future__ import annotations

from contextlib import suppress
import json
import logging
from pathlib import Path
import threading
import uuid

import psutil

logger = logging.getLogger(__name__)


class BootStatus:
    """One unique record per owner, retained until every nested boot request settles."""

    def __init__(self, home: Path):
        self.home = home.resolve()
        self.path = self.home / 'runtimes' / 'llamacpp' / 'booting' / f'{uuid.uuid4().hex}.json'
        self._lock = threading.Lock()
        self._count = 0
        self._closed = False

    def begin(self) -> None:
        from gateway.status import get_process_start_time

        with self._lock:
            if self._closed:
                return
            self._count += 1
            if self._count != 1:
                return
            try:
                proc = psutil.Process()
                record = {'pid': proc.pid, 'create_time': proc.create_time(),
                          'start_time': get_process_start_time(proc.pid),
                          'executable': proc.exe(), 'home': str(self.home)}
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                temporary = self.path.with_suffix('.tmp')
                try:
                    with temporary.open('x', encoding='utf-8') as stream:
                        json.dump(record, stream)
                    temporary.replace(self.path)
                finally:
                    with suppress(OSError):
                        temporary.unlink()
            except (OSError, ValueError, psutil.Error):
                logger.warning('Unable to publish managed boot observation', exc_info=True)

    def end(self) -> None:
        with self._lock:
            self._count = max(0, self._count - 1)
            if not self._count:
                with suppress(OSError):
                    self.path.unlink()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            with suppress(OSError):
                self.path.unlink()


def owner_boot_pending() -> bool:
    """Accept only a live process incarnation for this machine root; stale files grant nothing."""
    from hermes_cli.local_runtime.binaries import runtimes_root
    from hermes_cli.local_runtime.recovery import _same_birth, _valid_birth, _valid_pid

    root = runtimes_root()
    def modified(path):
        try:
            return path.stat().st_mtime
        except OSError:
            return 0

    try:
        paths = sorted((root / 'booting').glob('*.json'), key=modified, reverse=True)
    except OSError:
        return False
    for path in paths[:32]:
        try:
            with path.open(encoding='utf-8') as stream:
                record = json.loads(stream.read(4097))
            if (not isinstance(record, dict) or not _valid_pid(record.get('pid'))
                    or not _valid_birth(record.get('create_time'))
                    or Path(record.get('home', '')).resolve() != root.parent.parent.resolve()
                    or not isinstance(record.get('executable'), str)):
                continue
            proc = psutil.Process(record['pid'])
            if (proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
                    and _same_birth(proc, record) and proc.exe() == record['executable']):
                return True
        except (OSError, ValueError, TypeError, KeyError, OverflowError, psutil.Error):
            continue
    return False
