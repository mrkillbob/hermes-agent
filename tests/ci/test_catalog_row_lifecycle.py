"""Catalog files spend one outer-runner slot and retire each temporary runtime."""
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from tests.e2e.core.providers import _catalog_helpers as helpers


@pytest.fixture
def catalog_rows():
    return [helpers.Row(name, "chat_completions", "api_key", "FIXTURE_API_KEY",
                        "https://fixture.invalid/v1", (), True) for name in ("first", "second")]


@pytest.mark.parametrize("kind", ["matrix", "fallback"])
def test_catalog_rows_run_on_the_outer_worker_until_retirement(monkeypatch, tmp_path, catalog_rows, kind):
    # The caller owns the file's process-tree slot. A nested executor must not
    # start another row outside it, even if a small fixture happens to finish fast.
    from tests.e2e.core.providers import test_catalog_fallback as fallback

    retired = threading.Event()
    retired.set()
    owner = threading.get_ident()
    completed = []

    def drive(row, *_args):
        assert threading.get_ident() == owner, "row escaped the outer worker's process-tree slot"
        assert retired.is_set(), "previous row still owns a runtime"
        retired.clear()
        completed.append(row.name)
        retired.set()
        return {}

    factory = SimpleNamespace(mktemp=lambda name: tmp_path / name)
    if kind == "matrix":
        monkeypatch.setattr(helpers, "discover_catalog", lambda: catalog_rows)
        monkeypatch.setattr(helpers, "shard_rows", lambda _shard: catalog_rows)
        monkeypatch.setattr(helpers, "drive_turn", drive)
        result = helpers.drive_shard(0, factory)
    else:
        monkeypatch.setattr(fallback, "ROWS", catalog_rows)
        monkeypatch.setattr(fallback, "_drive", drive)
        result = fallback.results.__wrapped__(factory)
    assert list(result) == completed == [row.name for row in catalog_rows]
    assert retired.is_set()


@pytest.mark.parametrize("kind", ["matrix", "fallback"])
@pytest.mark.parametrize("outcome", [0, 1, "error"])
def test_row_retires_runtime_after_evidence_on_success_and_failure(
        monkeypatch, tmp_path, catalog_rows, kind, outcome):
    from tests.e2e.core.providers import test_catalog_fallback as fallback

    home = tmp_path / "home"
    home.mkdir()
    retired = threading.Event()
    evidence = threading.Event()
    usage = threading.Event()
    started = []

    def run(*_args, **kwargs):
        assert not started or retired.is_set(), "next row began before runtime retirement"
        retired.clear()
        started.append(True)
        kwargs["diagnostics"]["fixture_evidence"] = True
        evidence.set()
        if outcome == "error":
            raise RuntimeError("fixture launch failed")
        return subprocess.CompletedProcess([], outcome, helpers.FINAL, "")

    def retire(target):
        assert target == home
        assert evidence.is_set(), "cleanup erased the turn's evidence"
        if kind == "matrix" and outcome != "error":
            assert usage.is_set(), "cleanup preceded session usage collection"
        retired.set()

    def session_usage(_home):
        usage.set()
        return {"fixture_usage": True}

    monkeypatch.setattr(helpers, "retire_runtime", retire, raising=False)
    target = helpers if kind == "matrix" else fallback
    monkeypatch.setattr(target, "write_home", lambda *_args: home)
    monkeypatch.setattr(target, "run_hermes", run)
    monkeypatch.setattr(helpers, "session_usage", session_usage)
    monkeypatch.setattr(fallback, "CATALOG", catalog_rows)
    if kind == "matrix":
        call = lambda: helpers.drive_turn(catalog_rows[0], tmp_path, catalog_rows)
    else:
        call = lambda: fallback._drive(*catalog_rows, tmp_path)
    for _ in range(2):
        if outcome == "error":
            with pytest.raises(RuntimeError, match="fixture launch failed"):
                call()
        else:
            result = call()
            assert (result.rc if kind == "matrix" else result["rc"]) == outcome
        assert retired.is_set(), "row returned before its temporary runtime was retired"


@pytest.mark.platforms("linux")
def test_runtime_retirement_reaps_only_its_exact_temporary_home(tmp_path):
    homes = [helpers.write_home(tmp_path / name, {"provider": "custom"}) for name in ("owned", "neighbor")]
    children = []
    try:
        for home in homes:
            child = subprocess.Popen(
                [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.read()"],
                env=helpers.hermetic_env(home, {}), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            children.append(child)
            assert child.stdout.readline().strip() == "ready"
        helpers.retire_runtime(homes[0])
        children[0].wait(timeout=2)
        assert children[1].poll() is None, "cleanup crossed into another row's home"
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=2)


@pytest.mark.parametrize("target", ["", ".", "/", "unregistered"])
def test_runtime_retirement_refuses_unregistered_or_relative_homes(target, tmp_path):
    from pathlib import Path

    home = tmp_path / target if target == "unregistered" else Path(target)
    with pytest.raises(AssertionError, match="registered absolute synthetic home"):
        helpers.retire_runtime(home)


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("failure", ["ownership_changed", "access_denied"])
def test_runtime_retirement_rechecks_ownership_and_exposes_cleanup_failures(monkeypatch, tmp_path, failure):
    import psutil
    from tests.e2e.core.cli_tmux import _classic

    home = helpers.write_home(tmp_path, {"provider": "custom"})
    scans = iter([{123}, set()])
    monkeypatch.setattr(_classic, "home_pids", lambda _home: next(scans))

    class Process:
        def __init__(self, pid):
            assert pid == 123

        def environ(self):
            return {"HERMES_HOME": str(home / ".hermes") if failure == "access_denied" else "/another/home"}

        def terminate(self):
            if failure == "ownership_changed":
                pytest.fail("signalled a PID whose home changed after discovery")
            raise PermissionError("fixture cleanup denied")

    monkeypatch.setattr(psutil, "Process", Process)
    if failure == "access_denied":
        with pytest.raises(PermissionError, match="fixture cleanup denied"):
            helpers.retire_runtime(home)
    else:
        helpers.retire_runtime(home)
