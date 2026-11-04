"""Retire successful per-test Git upgrade installs within the runner attempt.

Register roots before expensive setup and wait for every pytest phase to finish.
Failure, skip, crash and explicit-keep evidence stays for existing runner cleanup.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path

import pytest


@dataclass
class _OwnedRoot:
    path: Path
    identity: tuple[int, int]
    keep: bool
    phases: dict[str, bool] = field(default_factory=dict)


_ROOT = pytest.StashKey[_OwnedRoot]()


@pytest.fixture
def upgrade_case_root(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> Path:
    # Short names leave room for the installed gateway's AF_UNIX socket.
    root = tmp_path_factory.mktemp("upg")
    info = root.lstat()
    request.node.stash[_ROOT] = _OwnedRoot(
        root, (info.st_dev, info.st_ino), bool(os.environ.get("HERMES_E2E_KEEP_ROOT"))
    )
    return root


def _writable_directories(fd: int) -> None:
    # uv cache files may be hardlinked. Only owned directories need write
    # permission for unlinking; never chmod regular files or follow symlinks.
    mode = stat.S_IMODE(os.fstat(fd).st_mode)
    os.fchmod(fd, mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    for name in os.listdir(fd):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            continue
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            opened = os.fstat(child)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise OSError(errno.ESTALE, "fixture directory changed identity", name)
            _writable_directories(child)
        finally:
            os.close(child)


def _retire_root(root: _OwnedRoot) -> None:
    fd = os.open(root.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != root.identity:
            raise OSError(errno.ESTALE, "fixture root changed identity", str(root.path))
        _writable_directories(fd)
    finally:
        os.close(fd)
    current = root.path.lstat()
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != root.identity:
        raise OSError(errno.ESTALE, "fixture root changed identity", str(root.path))
    shutil.rmtree(root.path)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item):
    result = yield
    report = result.get_result()
    root = item.stash.get(_ROOT, None)
    if root is None:
        return
    root.phases[report.when] = report.passed
    if report.when != "teardown":
        return
    del item.stash[_ROOT]
    if root.keep or os.environ.get("HERMES_E2E_KEEP_ROOT"):
        return
    if not all(root.phases.get(phase, False) for phase in ("setup", "call", "teardown")):
        return
    try:
        _retire_root(root)
    except OSError as exc:
        # A failed retirement is a real teardown failure, not a green test
        # followed by silent retained gigabytes. Leave remaining evidence.
        report.outcome = "failed"
        report.longrepr = f"Could not retire completed upgrade fixture {root.path}: {exc}"
