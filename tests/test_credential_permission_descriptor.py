"""Inert descriptor mocks exercise permission identity without pathname replacement."""

import os
import errno
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import utils


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("outcome", ["same", "different", "directory", "denied", "write-only", "unsupported", "stat-error", "chmod-error"])
def test_permission_repair_mutates_only_matching_regular_descriptor(monkeypatch, outcome, caplog):
    path = Path("inert-credential-store")
    observed = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_dev=17, st_ino=31)
    opened = SimpleNamespace(st_mode=observed.st_mode, st_dev=17,
                             st_ino=32 if outcome == "different" else 31)
    if outcome == "directory":
        opened.st_mode = stat.S_IFDIR | 0o750
    opens, mutations, closed, path_mutations = [], [], [], []

    def open_descriptor(candidate, flags):
        assert candidate == path
        opens.append(flags)
        if outcome == "denied" or outcome == "write-only" and not flags & os.O_WRONLY:
            raise PermissionError("inert denied descriptor")
        if outcome == "unsupported":
            raise OSError(errno.ENOTSUP, "inert unsupported descriptor")
        return 91

    def mutate_descriptor(fd, mode):
        mutations.append((fd, mode))
        if outcome == "chmod-error":
            raise PermissionError("inert read-only descriptor")

    monkeypatch.setattr(Path, "lstat", lambda _path: observed)
    monkeypatch.setattr(Path, "chmod", lambda *args, **kwargs: path_mutations.append(args))
    monkeypatch.setattr(os, "open", open_descriptor)
    def inspect_descriptor(_fd):
        if outcome == "stat-error":
            raise OSError("inert failed descriptor inspection")
        return opened

    monkeypatch.setattr(os, "fstat", inspect_descriptor)
    monkeypatch.setattr(os, "fchmod", mutate_descriptor)
    monkeypatch.setattr(os, "close", closed.append)
    utils.restrict_credential_file_permissions(path)

    assert opens
    assert all(flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK for flags in opens)
    assert all(not flags & (os.O_CREAT | os.O_TRUNC) for flags in opens)
    assert path_mutations == []
    assert mutations == ([(91, 0o600)] if outcome in {"same", "write-only", "chmod-error"} else [])
    assert closed == ([] if outcome in {"denied", "unsupported"} else [91])
    if outcome in {"denied", "write-only"}:
        assert len(opens) == 2 and opens[-1] & os.O_WRONLY
    if outcome in {"denied", "unsupported", "stat-error", "chmod-error"}:
        assert "Could not restrict credential file permissions" in caplog.text
