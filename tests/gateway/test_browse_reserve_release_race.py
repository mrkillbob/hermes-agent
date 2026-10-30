"""Boot must retry a real reservation when maintenance releases between conflict probes."""
from contextlib import contextmanager
import os
import select
import subprocess
import sys

import pytest


@contextmanager
def _maintenance_holder(home):
    program = """
from pathlib import Path
import sys
from gateway.runtime_ownership import ProfileOwnership
from hermes_state_reconcile import BROWSE_RECONCILE_PURPOSE
owner = ProfileOwnership()
try:
    owner.reserve([Path(sys.argv[1])], purpose=BROWSE_RECONCILE_PURPOSE)
    print('held', flush=True)
    sys.stdin.read()
finally:
    owner.close()
"""
    child = subprocess.Popen([sys.executable, '-c', program, str(home)], env=os.environ,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)

    def release():
        stdout, stderr = child.communicate(input='', timeout=10)
        assert child.returncode == 0, stdout + stderr

    try:
        assert select.select([child.stdout], [], [], 10)[0], 'maintenance holder did not become ready'
        assert child.stdout.readline().strip() == 'held'
        yield release
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


@pytest.mark.platforms('linux', 'macos')
@pytest.mark.parametrize('multiple_homes', [False, True])
def test_boot_retries_when_maintenance_releases_before_conflict_probe(tmp_path, monkeypatch, multiple_homes):
    import gateway.run_bootstrap as bootstrap
    from gateway.runtime_ownership import ProfileOwnership

    held = tmp_path / 'b-maintenance'
    homes = [tmp_path / 'a-free', held] if multiple_homes else [held]
    booting = ProfileOwnership()
    original = bootstrap._conflicting_homes
    with _maintenance_holder(held) as release:
        def release_before_probe(requested):
            # The first real reserve failed. Its partial multi-home reservation must be gone.
            assert not booting.homes
            release()
            conflicting = original(requested)
            assert conflicting == []
            return conflicting

        monkeypatch.setattr(bootstrap, '_conflicting_homes', release_before_probe)
        try:
            bootstrap._reserve_waiting_out_browse_reconcile(booting, homes)
            assert all(booting.owns(home) for home in homes)
        finally:
            booting.close()


@pytest.mark.platforms('linux', 'macos')
def test_release_gap_does_not_bypass_a_new_runtime_owner(tmp_path, monkeypatch):
    import gateway.run_bootstrap as bootstrap
    from gateway.runtime_ownership import OwnershipConflict, ProfileOwnership

    held = tmp_path / 'b-maintenance'
    homes = [tmp_path / 'a-free', held]
    booting, runtime = ProfileOwnership(), ProfileOwnership()
    original = bootstrap._conflicting_homes
    probes = []
    with _maintenance_holder(held) as release:
        def replace_before_probe(requested):
            release()
            runtime.reserve([held])
            conflicting = original(requested)
            probes.append(conflicting)
            return conflicting

        monkeypatch.setattr(bootstrap, '_conflicting_homes', replace_before_probe)
        try:
            with pytest.raises(OwnershipConflict):
                bootstrap._reserve_waiting_out_browse_reconcile(booting, homes)
            assert probes == [[held]]
            assert not booting.homes
            assert runtime.owns(held)
            free = ProfileOwnership()
            try:
                free.reserve([homes[0]])
                assert free.owns(homes[0])
            finally:
                free.close()
        finally:
            booting.close()
            runtime.close()


@pytest.mark.platforms('linux', 'macos')
def test_repeated_release_gaps_keep_the_original_boot_deadline(tmp_path, monkeypatch):
    import time
    import gateway.run_bootstrap as bootstrap
    from gateway.runtime_ownership import OwnershipConflict, ProfileOwnership
    from hermes_state_reconcile import BROWSE_RECONCILE_PURPOSE

    home = tmp_path / 'churning-maintenance'
    holder, booting = ProfileOwnership(), ProfileOwnership()
    original = bootstrap._conflicting_homes
    probes = []

    def release_and_reacquire(requested):
        holder.close()
        conflicting = original(requested)
        assert conflicting == []
        probes.append(conflicting)
        holder.reserve([home], purpose=BROWSE_RECONCILE_PURPOSE)
        return conflicting

    holder.reserve([home], purpose=BROWSE_RECONCILE_PURPOSE)
    monkeypatch.setattr(bootstrap, '_conflicting_homes', release_and_reacquire)
    wait = bootstrap._browse_reconcile_reserve_wait_s()
    started = time.monotonic()
    try:
        with pytest.raises(OwnershipConflict):
            bootstrap._reserve_waiting_out_browse_reconcile(booting, [home])
        assert len(probes) > 1
        assert wait <= time.monotonic() - started < wait + 5
        assert not booting.homes
        assert holder.owns(home)
    finally:
        booting.close()
        holder.close()
