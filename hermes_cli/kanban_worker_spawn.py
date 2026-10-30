"""One launch seam for managed workers; Darwin must not fork a threaded gateway."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from hermes_platform.host.facts import os_family


@dataclass(frozen=True)
class SpawnedWorker:
    pid: int


def spawn_worker(argv, *, cwd, stdin, stdout, stderr, env, start_new_session, creationflags):
    if os_family() != "darwin":
        return subprocess.Popen(argv, cwd=cwd, stdin=stdin, stdout=stdout, stderr=stderr,
                                env=env, start_new_session=start_new_session, creationflags=creationflags)
    if stdin != subprocess.DEVNULL or stderr != subprocess.STDOUT or not start_new_session or creationflags:
        raise ValueError("unsupported native managed-worker launch options")
    # Raw libc would bypass Python's existing fleet/startup audit fences.
    sys.audit("subprocess.Popen", argv[0], list(argv), cwd, env)
    from hermes_platform.darwin_spawn import spawn_detached
    return SpawnedWorker(spawn_detached(argv, cwd=cwd, env=env, log_fd=stdout.fileno()))
