"""Import supervision stays bounded even when an owned import closes its reporting pipe."""

import json
import os
import subprocess
import sys

import pytest

from tests.e2e.core.upgrade.test_fresh_process_entrypoints import _IMPORT_RUNNER


@pytest.mark.platforms("posix")
def test_closed_reporting_pipe_does_not_disable_import_timeout(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "closed_pipe.py").write_text("import os, time\nos.closerange(3, 256)\ntime.sleep(5)\n")
    (tree / "healthy.py").write_text("value = 1\n")
    runner = tmp_path / "runner.py"
    runner.write_text(_IMPORT_RUNNER)
    out = tmp_path / "results.json"
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "tree": str(tree), "first_party": ["closed_pipe", "healthy"], "preload": [],
        "modules": [{"id": name, "kind": "name", "name": name} for name in ("closed_pipe", "healthy")],
        "workers": 1, "timeout": 0.5, "out": str(out),
    }))
    env = {"HOME": str(tmp_path), "HERMES_HOME": str(tmp_path / "home"), "PATH": os.environ["PATH"]}
    result = subprocess.run([sys.executable, "-I", str(runner), str(spec)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=3, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    results = {entry["id"]: entry for entry in json.loads(out.read_text())}
    assert results["closed_pipe"]["type"] == "Timeout"
    assert results["healthy"]["ok"] is True
    assert "closed_pipe" in result.stderr
