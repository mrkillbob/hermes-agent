"""Unreadable lease namespaces are unknown ownership, never proof of no reader."""
import contextlib
import errno
import os

import pytest

from hermes_cli.runtime_state import leases_held


@pytest.mark.parametrize("error", [
    PermissionError(errno.EACCES, "synthetic unreadable lease directory"),
    OSError(errno.EIO, "synthetic lease enumeration failure"),
    NotADirectoryError(errno.ENOTDIR, "synthetic replaced lease directory"),
], ids=["permission", "io", "not-directory"])
def test_unreadable_lease_namespace_is_held(tmp_path, monkeypatch, error):
    generation = tmp_path / "generation"
    leases = generation / ".leases"
    leases.mkdir(parents=True)
    real_scandir = os.scandir

    def unreadable(path):
        if str(path) == str(leases):
            raise error
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", unreadable)
    assert leases_held(generation), "failed enumeration must not certify the generation as idle"


def test_absent_lease_namespace_is_not_a_live_hold(tmp_path):
    assert not leases_held(tmp_path / "generation")


def test_incomplete_enumeration_preserves_already_seen_entries(tmp_path, monkeypatch):
    generation = tmp_path / "generation"
    leases = generation / ".leases"
    leases.mkdir(parents=True)
    stale = leases / "stale"
    stale.touch()
    real_scandir = os.scandir
    with real_scandir(leases) as directory:
        entry = next(directory)

    @contextlib.contextmanager
    def interrupted_entries():
        def entries():
            yield entry
            raise OSError(errno.EIO, "synthetic interrupted enumeration")
        yield entries()

    def interrupted(path):
        return interrupted_entries() if str(path) == str(leases) else real_scandir(path)

    monkeypatch.setattr(os, "scandir", interrupted)
    assert leases_held(generation)
    assert stale.exists(), "incomplete enumeration must not begin pruning"
