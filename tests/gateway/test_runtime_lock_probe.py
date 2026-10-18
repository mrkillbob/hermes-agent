"""A losing native lock probe cannot modify another owner's identity record.

The child pauses the real reservation at JSON publication, after it has locked and
truncated the inode. No gateway, service, or simulated host API is involved.
"""

from __future__ import annotations

import builtins
import os
import queue
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path

import psutil
import pytest

from gateway import status


_HOLDER = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, {root!r})
from gateway.runtime_ownership import ProfileOwnership
print(os.getpid(), flush=True)
original_dump = json.dump
def publish(record, handle):
    print('reserved', flush=True)
    if sys.stdin.readline().strip() != 'publish':
        raise RuntimeError('publication not requested')
    original_dump(record, handle)
json.dump = publish
owner = ProfileOwnership()
try:
    owner.reserve([Path(os.environ['HERMES_HOME'])])
    print('published', flush=True)
    sys.stdin.read()
finally:
    owner.close()
"""


def _readline(stream):
    got = queue.Queue()
    threading.Thread(target=lambda: got.put(stream.readline()), daemon=True).start()
    try:
        return got.get(timeout=10)
    except queue.Empty:
        return ""


def _stop(proc, holder):
    # EOF releases a blocked publication or the final read, including on a
    # Windows venv launcher. Never enumerate unrelated host processes.
    try:
        proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        if holder is not None:
            with suppress(psutil.Error):
                holder.kill()
        proc.kill()
        proc.communicate(timeout=10)


@pytest.fixture
def paused_reservation(tmp_path):
    home = tmp_path / "Jörg Ñúñez profile"
    script = tmp_path / "lock_holder.py"
    script.write_text(
        _HOLDER.format(root=str(Path(__file__).resolve().parents[2])), encoding="utf-8"
    )
    proc = subprocess.Popen(
        [sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env={**os.environ, "HERMES_HOME": str(home)},
    )
    holder = None
    try:
        assert proc.stdout is not None and proc.stdin is not None
        pid = _readline(proc.stdout).strip()
        assert pid.isdigit(), "reservation child did not report its interpreter PID"
        holder = psutil.Process(int(pid))
        assert _readline(proc.stdout).strip() == "reserved"
        published = False

        def publish():
            nonlocal published
            if not published:
                proc.stdin.write("publish\n")
                proc.stdin.flush()
                assert _readline(proc.stdout).strip() == "published"
                published = True

        yield home / "gateway.lock", holder.pid, publish
    finally:
        _stop(proc, holder)


@pytest.mark.platforms("any")
def test_losing_probe_leaves_held_empty_record_unchanged(paused_reservation):
    path, _pid, _publish = paused_reservation

    assert status._is_gateway_runtime_lock_active_strict(path) is True

    assert path.stat().st_size == 0, "a losing probe wrote into the owner's publication window"


@pytest.mark.platforms("any")
def test_published_reservation_roundtrips_while_contenders_are_excluded(paused_reservation):
    path, pid, publish = paused_reservation
    publish()
    record = status._read_gateway_lock_record(path)
    assert record is not None and record["pid"] == pid
    assert Path(record["hermes_home"]) == path.parent.resolve()

    assert status._is_gateway_runtime_lock_active_strict(path) is True

    assert status._read_gateway_lock_record(path) == record


class _PublishDuringProbe:
    """Synchronize real I/O without replacing the native locking operation."""

    def __init__(self, handle, publish):
        self.handle = handle
        self.publish = publish

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def tell(self):
        position = self.handle.tell()
        self.publish()
        return position

    def seek(self, offset, whence=os.SEEK_SET):
        if offset == status._WINDOWS_LOCK_OFFSET and whence == os.SEEK_SET:
            self.publish()
        return self.handle.seek(offset, whence)


@pytest.mark.platforms("windows")
@pytest.mark.parametrize("strict", [True, False], ids=["strict-rplus", "ordinary-append"])
def test_losing_probe_cannot_clobber_completed_owner_publication(paused_reservation, monkeypatch, strict):
    path, pid, publish = paused_reservation
    # On the old path, publish after EOF was observed but before the preliminary
    # newline write. On the corrected path, publish before the byte-range probe.
    # Let the production caller choose its real open mode, buffering and encoding:
    # r+ can overwrite a prefix, whereas append mode writes harmless trailing space.
    def synchronized_open(*args, **kwargs):
        return _PublishDuringProbe(builtins.open(*args, **kwargs), publish)

    monkeypatch.setattr(status, "open", synchronized_open, raising=False)
    probe = status._is_gateway_runtime_lock_active_strict if strict else status.is_gateway_runtime_lock_active
    assert probe(path) is True

    record = status._read_gateway_lock_record(path)
    assert record is not None, "a losing probe corrupted the completed owner record"
    assert record["pid"] == pid
