"""A completed catalog turn must settle and exit without leaking its child process."""

from __future__ import annotations

import os
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.e2e.core.providers import _catalog_helpers as H


@pytest.mark.platforms("linux")
def test_completed_catalog_turn_exits(tmp_path, monkeypatch):
    catalog = H.discover_catalog()
    row = next(row for row in catalog if row.name == "commandcode")
    trace = tmp_path / "trace"
    trace.mkdir()
    (trace / "sitecustomize.py").write_text(
        "import faulthandler\nfaulthandler.dump_traceback_later(45, repeat=True)\n",
        encoding="utf-8",
    )
    run_hermes = H.run_hermes

    def traced_run(home, cwd, env_extra, *args, **kwargs):
        return run_hermes(home, cwd, {
            **env_extra,
            "PYTHONPATH": os.pathsep.join((str(trace), str(H.REPO_ROOT))),
        }, *args, **kwargs)

    monkeypatch.setattr(H, "run_hermes", traced_run)
    turn = H.drive_turn(row, tmp_path / "turn", catalog)
    cells = H.evaluate(turn, catalog)
    if turn.rc != 0 or H.FINAL not in turn.stdout or not all(cells.values()):
        print(turn.stderr, flush=True)
    assert H.FINAL in turn.stdout, turn.detail()
    assert turn.rc == 0, turn.detail()
    assert all(cells.values()), turn.detail()


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("workers", [24, 6], ids=["load24", "load6"])
def test_catalog_exit_under_recorded_fanout(tmp_path, monkeypatch, workers):
    catalog = H.discover_catalog()
    names = {"commandcode", "kimi-coding-cn", "minimax-cn", "nebius-token-factory",
             "alibaba-cn", "alibaba-token-plan", "meta-ai", "deepinfra"}
    rows = [row for row in catalog if row.name in names]
    assert len(rows) == len(names)
    trace = tmp_path / "trace"
    trace.mkdir()
    (trace / "sitecustomize.py").write_text(
        "import faulthandler\nfaulthandler.dump_traceback_later(45, repeat=True)\n",
        encoding="utf-8",
    )
    run_hermes = H.run_hermes

    def traced_run(home, cwd, env_extra, *args, **kwargs):
        return run_hermes(home, cwd, {
            **env_extra,
            "PYTHONPATH": os.pathsep.join((str(trace), str(H.REPO_ROOT))),
        }, *args, **kwargs)

    monkeypatch.setattr(H, "run_hermes", traced_run)

    def drive(item):
        index, row = item
        started = time.time()
        turn = H.drive_turn(row, tmp_path / f"turn-{index}-{row.name}", catalog)
        failed = [cell for cell, ok in H.evaluate(turn, catalog).items() if not ok]
        print(json.dumps({"workers": workers, "copy": index, "provider": row.name,
                          "wall_s": turn.wall_s, "rc": turn.rc,
                          "final_printed": H.FINAL in turn.stdout, "failed": failed,
                          "post_seconds": [round(r.t - started, 2) for r in turn.requests
                                           if r.method == "POST"]}), flush=True)
        if failed:
            print(f"FULL STDERR {index} {row.name}\n{turn.stderr}", flush=True)
        return row.name, failed

    # Existing main E2E permits three files, each with eight CLI children. Compare that
    # observed ceiling with six children while retaining every real turn and assertion.
    items = [(index, row) for index in range(3) for row in rows]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(drive, items))
    assert not [(name, failed) for name, failed in outcomes if failed]
