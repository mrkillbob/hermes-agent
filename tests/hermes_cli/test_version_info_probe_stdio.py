"""Git identity probes must not inherit RPC input or drain descendant-held pipes."""

import os
import stat
import subprocess
import sys
import time

import pytest

from hermes_cli import version_info


def _noninteractive_output(options):
    assert options.get("stdin") == subprocess.DEVNULL, "identity probe inherits the RPC input"
    assert options.get("stderr") == subprocess.DEVNULL
    output = options.get("stdout")
    assert hasattr(output, "fileno"), "captured pipes can outlive the timed-out Git parent"
    assert stat.S_ISREG(os.fstat(output.fileno()).st_mode)
    assert not options.get("capture_output")
    assert options["timeout"] == 3
    return output


@pytest.mark.parametrize(("returncode", "output", "expected"), [
    (0, "fixture-head\n", "fixture-head"),
    (0, "  分支名\n", "分支名"),
    (0, "line1\rline2\r\n", "line1\nline2"),
    (0, b"head-\xff\r\n", "head-\ufffd"),
    (0, "", None),
    (1, "not-authoritative\n", None),
])
def test_git_result_preserves_identity_semantics_without_pipe_capture(monkeypatch, tmp_path, returncode, output, expected):
    monkeypatch.setattr(version_info, "_git_probe", lambda command: (command, None))

    def run(command, **options):
        _noninteractive_output(options).write(output.encode("utf-8") if isinstance(output, str) else output)
        assert command == ["git", "rev-parse", "HEAD"]
        assert options["cwd"] == str(tmp_path)
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(version_info.subprocess, "run", run)
    assert version_info._run_git(tmp_path, "rev-parse", "HEAD") == expected


@pytest.mark.parametrize("error", [OSError("synthetic missing Git"), subprocess.TimeoutExpired("git", 3)])
def test_git_unavailable_or_timeout_remains_unknown(monkeypatch, tmp_path, error):
    monkeypatch.setattr(version_info, "_git_probe", lambda command: (command, None))

    def run(command, **options):
        _noninteractive_output(options).write(b"partial-untrusted-output")
        raise error

    monkeypatch.setattr(version_info.subprocess, "run", run)
    assert version_info._run_git(tmp_path, "rev-parse", "HEAD") is None


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize("large", [False, True], ids=["small", "large"])
def test_dirty_probe_uses_the_same_bounded_stdio(monkeypatch, tmp_path, returncode, large):
    def git(_root, *args):
        return {"rev-parse": "abcdef123", "branch": "main", "log": "123", "tag": "v1.0.0",
                "rev-list": "0"}.get(args[0])

    monkeypatch.setattr(version_info, "_run_git", git)
    monkeypatch.setattr(version_info, "_git_probe", lambda command: (command, None))

    def run(command, **options):
        _noninteractive_output(options).write(b" M tracked.py\n" * (100_000 if large else 1))
        assert command == ["git", "--no-optional-locks", "status", "--porcelain", "-uno"]
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(version_info.subprocess, "run", run)
    assert version_info._git_version_info(tmp_path).dirty is (returncode == 0)


def test_real_probe_child_gets_eof_and_returns_utf8(monkeypatch, tmp_path):
    code = "import sys; assert sys.stdin.read() == ''; print('fixture-分支')"
    monkeypatch.setattr(version_info, "_git_probe", lambda _command: ([sys.executable, "-c", code], None))
    assert version_info._run_git(tmp_path, "rev-parse", "HEAD") == "fixture-分支"


def test_oversized_git_output_is_unknown_not_a_truncated_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(version_info, "_git_probe", lambda command: (command, None))

    def run(command, **options):
        _noninteractive_output(options).write(b"a" * (2 * 1024 * 1024))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(version_info.subprocess, "run", run)
    assert version_info._run_git(tmp_path, "rev-parse", "HEAD") is None


@pytest.mark.parametrize("change", ["grow", "short"])
def test_snapshot_size_mismatch_cannot_publish_partial_identity(monkeypatch, tmp_path, change):
    real_output = version_info.tempfile.TemporaryFile

    class GrowingOutput:
        def __init__(self):
            self.inner = real_output()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.inner.close()

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def read(self, size):
            assert size == 13, "read must be limited to the finite captured-size snapshot"
            captured = self.inner.read(size)
            if change == "short":
                return captured[:-1]
            self.inner.seek(0, 2)
            self.inner.write(b"extra")
            self.inner.flush()
            return captured

    monkeypatch.setattr(version_info.tempfile, "TemporaryFile", GrowingOutput)
    monkeypatch.setattr(version_info, "_git_probe", lambda command: (command, None))

    def run(command, **options):
        _noninteractive_output(options).write(b"fixture-head\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(version_info.subprocess, "run", run)
    assert version_info._run_git(tmp_path, "rev-parse", "HEAD") is None


@pytest.mark.parametrize("slow_parent", [False, True])
@pytest.mark.platforms("windows", "macos", "linux")
def test_probe_does_not_drain_a_descendant_retained_output_handle(monkeypatch, tmp_path, slow_parent):
    ready, stop, closed = (tmp_path / name for name in ("ready", "stop", "closed"))
    child = """\
import pathlib, sys, time
ready, stop, closed = map(pathlib.Path, sys.argv[1:])
ready.write_text('ready')
deadline = time.monotonic() + 12
while not stop.exists() and time.monotonic() < deadline:
    time.sleep(0.02)
closed.write_text('closed')
"""
    parent = """\
import pathlib, subprocess, sys, time
ready, stop, closed = map(pathlib.Path, sys.argv[1:4])
subprocess.Popen([sys.executable, '-c', sys.argv[4], str(ready), str(stop), str(closed)],
                 stdin=subprocess.DEVNULL)
deadline = time.monotonic() + 5
while not ready.exists() and time.monotonic() < deadline:
    time.sleep(0.02)
assert ready.exists()
print('fixture-head', flush=True)
if sys.argv[5] == 'slow':
    time.sleep(12)
"""
    command = [sys.executable, "-c", parent, str(ready), str(stop), str(closed), child,
               "slow" if slow_parent else "fast"]
    monkeypatch.setattr(version_info, "_git_probe", lambda _command: (command, None))
    started = time.monotonic()
    try:
        result = version_info._run_git(tmp_path, "rev-parse", "HEAD")
        assert result == (None if slow_parent else "fixture-head")
        assert time.monotonic() - started < 8, "probe waited for the descendant-held output pipe"
        assert ready.exists() and not closed.exists(), "retained-handle control was not alive"
    finally:
        # Only our fixture observes this stop file; no PID lookup or broad termination.
        stop.touch()
        deadline = time.monotonic() + 15
        while not closed.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert closed.exists(), "the bounded fixture descendant did not finish"
