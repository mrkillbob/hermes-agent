"""A completed catalog turn must settle and exit without leaking its child process."""

from __future__ import annotations

import os

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
