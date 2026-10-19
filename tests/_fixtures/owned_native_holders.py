"""Project native descriptor inventories onto explicitly registered disposable test processes."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest


class OwnedNativeHolders:
    def __init__(self, manifest):
        self.manifest = Path(manifest)
        record = json.dumps([os.getpid(), psutil.Process().create_time()]) + '\n'
        fd = os.open(self.manifest, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, record.encode())
        finally:
            os.close(fd)

    def processes(self):
        # Creation time excludes a recycled PID after a fixture child has exited.
        records = self.manifest.read_text().splitlines(keepends=True)
        for record in records:
            if not record.endswith('\n'):
                continue  # another owned child is still appending its single record
            pid, birth = json.loads(record)
            try:
                process = psutil.Process(pid)
                if process.create_time() == birth:
                    yield process
            except psutil.NoSuchProcess:
                continue

    def pids(self, _lib=None):
        return [process.pid for process in self.processes()]

    def process_iter(self, attrs):
        for process in self.processes():
            yield SimpleNamespace(info=process.as_dict(attrs=attrs))

    def listdir(self, path):
        return [str(pid) for pid in self.pids()] if path == '/proc' else os.listdir(path)

    def install(self, set_attribute):
        import hermes_state_dbfile as dbfile
        import hermes_state_holders as holders

        projected_psutil = SimpleNamespace(**vars(psutil))
        projected_psutil.process_iter = self.process_iter
        projected_os = SimpleNamespace(**vars(os))
        projected_os.listdir = self.listdir
        set_attribute(holders, 'psutil', projected_psutil)
        set_attribute(holders, 'os', projected_os)
        set_attribute(dbfile, 'os', projected_os)
        set_attribute(dbfile, '_darwin_all_pids', self.pids)

    def bootstrap(self, script):
        return (f'import sys\nsys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n'
                'from tests._fixtures.owned_native_holders import install_child_projection\n'
                f'install_child_projection({str(self.manifest)!r})\n' + script)


def install_child_projection(manifest):
    """Only explicit fixture scripts call this; no environment flag alters production policy."""
    scope = OwnedNativeHolders(manifest)
    scope.install(setattr)


@pytest.fixture
def owned_native_holders(monkeypatch, tmp_path, request):
    """Keep real OS descriptor reads for this test and its explicit helper children."""
    scope = OwnedNativeHolders(tmp_path / 'owned-holder-pids.jsonl')
    scope.install(monkeypatch.setattr)
    for name in ('_PROC_BROWSE', '_PURPOSE_HOLDER', '_RAW_FLOCK_HOLDER', '_SLOW_BROWSE',
                 '_SLOW_SCAN_BROWSE', '_LEGACY_GATEWAY', '_MULTIPLEXER_LOCK'):
        script = getattr(request.module, name, None)
        if script is not None:
            monkeypatch.setattr(request.module, name, scope.bootstrap(script))
    return scope
