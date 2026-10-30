"""Foreground terminal output survives a busy POSIX gateway's high pipe descriptors."""

import os
import subprocess
import sys

import pytest

from tools.environments.base import BaseEnvironment


class _PipeEnvironment(BaseEnvironment):
    def cleanup(self):
        pass


@pytest.mark.platforms("posix")
def test_foreground_terminal_captures_stdout_and_stderr_above_select_limit(tmp_path):
    import fcntl
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft <= 1024:
        if hard != resource.RLIM_INFINITY and hard <= 1024:
            pytest.skip("host descriptor hard limit cannot accommodate a high pipe")
        resource.setrlimit(resource.RLIMIT_NOFILE, (1025, hard))
    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; print('stdout sentinel'); print('stderr sentinel', file=sys.stderr); sys.exit(7)"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        high_fd = fcntl.fcntl(proc.stdout.fileno(), fcntl.F_DUPFD, 1024)
        proc.stdout.close()
        proc.stdout = os.fdopen(high_fd, "r", encoding="utf-8")

        env = _PipeEnvironment(cwd=str(tmp_path), timeout=10)
        result = env._wait_for_process(proc, timeout=10, bounded_capture=True)

        assert result["returncode"] == 7
        assert "stdout sentinel" in result["output"]
        assert "stderr sentinel" in result["output"]
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            proc.stdout.close()
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
