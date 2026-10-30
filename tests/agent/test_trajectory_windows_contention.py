"""Observe real Windows lock exhaustion without losing a trajectory record."""

import errno
import gzip
import json
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread

import pytest

pytestmark = pytest.mark.platforms("windows")
ROOT = str(Path(__file__).resolve().parents[2])


def _events(process):
    queue = Queue()

    def read():
        for line in process.stdout:
            queue.put(line)
        queue.put(None)

    reader = Thread(target=read, daemon=True)
    reader.start()
    return queue, reader


def test_real_acquisition_exhaustion_does_not_drop_the_record(tmp_path):
    """Release the holder only after the real CRT reports acquisition exhaustion.

    No simulated contention, timing sleep, or changed lock call: the writer's wrapper
    only reports the real raising operation before re-raising its original exception.
    """
    target = tmp_path / "contended.jsonl.gz"
    holder_script = f"""
import json, os, sys
sys.path.insert(0, {ROOT!r})
from agent.trajectory import _lock_append_handle
with open({str(target) + '.lock'!r}, 'a+b') as handle:
    _lock_append_handle(handle, True)
    print(json.dumps({{'phase': 'holder_locked', 'pid': os.getpid()}}), flush=True)
    sys.stdin.readline()
    _lock_append_handle(handle, False)
"""
    writer_script = f"""
import json, msvcrt, os, sys, time
sys.path.insert(0, {ROOT!r})
from agent.trajectory import save_trajectory
real_lock = msvcrt.locking
def observed_lock(fd, mode, count):
    started = time.monotonic()
    try:
        return real_lock(fd, mode, count)
    except OSError as error:
        print(json.dumps({{'phase': 'locking_error', 'pid': os.getpid(),
                          'mode': mode, 'errno': error.errno,
                          'elapsed_s': time.monotonic() - started}}), flush=True)
        raise
msvcrt.locking = observed_lock
save_trajectory([{{'from': 'human', 'value': 'after-holder'}}], 'm', True, {str(target)!r})
print(json.dumps({{'phase': 'writer_returned', 'pid': os.getpid()}}), flush=True)
"""
    processes, readers = [], []
    try:
        holder = subprocess.Popen([sys.executable, "-c", holder_script], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes.append(holder)
        holder_events, reader = _events(holder)
        readers.append(reader)
        assert json.loads(holder_events.get(timeout=30))["phase"] == "holder_locked"

        writer = subprocess.Popen([sys.executable, "-c", writer_script], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes.append(writer)
        writer_events, reader = _events(writer)
        readers.append(reader)
        event = json.loads(writer_events.get(timeout=30))
        print(json.dumps(event), flush=True)
        assert event["phase"] == "locking_error", event
        assert event["errno"] == errno.EDEADLK, event
        import msvcrt
        assert event["mode"] == msvcrt.LK_LOCK, event

        holder.stdin.write("release\n")
        holder.stdin.flush()
        assert holder.wait(timeout=30) == 0, holder.stderr.read()
        assert writer.wait(timeout=30) == 0, writer.stderr.read()
        assert target.exists(), "save_trajectory returned without publishing the contended record"
        with gzip.open(target, "rt", encoding="utf-8") as stream:
            entries = [json.loads(line) for line in stream]
        assert [row["conversations"][0]["value"] for row in entries] == ["after-holder"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=2)
        for process in processes:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def test_non_contention_acquisition_error_is_not_retried(tmp_path, monkeypatch):
    import msvcrt
    from agent.trajectory import _lock_append_handle

    failure, calls = OSError(errno.EBADF, "invalid fixture descriptor"), []

    def fail(*args):
        calls.append(args)
        raise failure

    monkeypatch.setattr(msvcrt, "locking", fail)
    with (tmp_path / "bad.lock").open("a+b") as handle, pytest.raises(OSError) as caught:
        _lock_append_handle(handle, True)
    assert caught.value is failure
    assert len(calls) == 1


def test_release_error_is_not_retried(tmp_path, monkeypatch):
    import msvcrt
    from agent.trajectory import _lock_append_handle

    failure, calls = OSError(errno.EDEADLK, "release fixture failure"), []

    def fail(*args):
        calls.append(args)
        raise failure

    monkeypatch.setattr(msvcrt, "locking", fail)
    with (tmp_path / "release.lock").open("a+b") as handle, pytest.raises(OSError) as caught:
        _lock_append_handle(handle, False)
    assert caught.value is failure
    assert len(calls) == 1
