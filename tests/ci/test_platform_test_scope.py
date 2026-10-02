"""Native Windows results are advisory only in this fork's ordinary CI.

Shared checks stay required regardless of the OS that hosts their runner.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ci/platform_test_scope.py"


def _module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_scope_preserves_full_coverage_and_rejects_invalid_scope(tmp_path):
    def run(workflow, scope):
        output = tmp_path / f"{workflow}-{scope}.txt"
        child = subprocess.run(
            [sys.executable, str(SCRIPT), "plan", "--workflow", workflow, "--scope", scope],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "GITHUB_OUTPUT": str(output)},
        )
        return child, dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}

    all_os, full = run("os", "all")
    mac_os, mac = run("os", "macos")
    windows_os, windows = run("os", "windows")
    assert all_os.returncode == mac_os.returncode == windows_os.returncode == 0
    full_rows = json.loads(full["matrix"])["include"]
    mac_rows = json.loads(mac["matrix"])["include"]
    win_rows = json.loads(windows["matrix"])["include"]
    assert full_rows == mac_rows + win_rows
    assert mac_rows and {row["marker"] for row in mac_rows} == {"macos"}
    assert len(win_rows) == 2 and {row["marker"] for row in win_rows} == {"windows"}
    assert mac["run_windows"] == "false" and windows["run_windows"] == "true"
    assert all(row.get("runner") for row in full_rows)

    for scope, posix, native_windows in [("all", "true", "true"), ("posix", "true", "false"), ("windows", "false", "true")]:
        child, plan = run("bootstrap", scope)
        assert child.returncode == 0, child.stderr
        assert (plan["run_posix"], plan["run_windows"]) == (posix, native_windows)
    for workflow, scope in [("os", "linux"), ("os", "posix"), ("bootstrap", "macos"), ("bootstrap", "unknown")]:
        child, output = run(workflow, scope)
        assert child.returncode != 0 and not output


@pytest.mark.parametrize("repository,release,ref_type,windows_required", [
    pytest.param("mrkillbob/hermes-agent", False, "branch", False, id="fork-source"),
    pytest.param("mrkillbob/hermes-agent", True, "branch", True, id="fork-release"),
    pytest.param("mrkillbob/hermes-agent", False, "tag", True, id="fork-tag"),
    pytest.param("NousResearch/hermes-agent", False, "branch", True, id="upstream"),
    pytest.param("someone/hermes-agent", False, "branch", True, id="other-fork"),
])
@pytest.mark.parametrize("failed_job", ["windows-native", "macos-native", "posix-native", "tests", "js-tests", "rust-tests", "e2e-desktop-core", "supply-chain", "case-collision-check"])
@pytest.mark.parametrize("result", ["failure", "cancelled", "unknown"])
def test_platform_only_advisory_gate(repository, release, ref_type, windows_required, failed_job, result):
    workflow = YAML(typ="safe").load((ROOT / ".github/workflows/ci.yaml").read_text())
    jobs = workflow["jobs"]
    required = jobs["all-checks-pass"]["needs"]
    outputs = _module(SCRIPT).policy_scopes(repository, release, ref_type) if SCRIPT.exists() else {}

    def scope(job):
        value = jobs[job].get("with", {}).get("platform_scope", "all")
        match = re.fullmatch(r"\$\{\{\s*needs\.detect\.outputs\.([\w-]+)\s*\}\}", value)
        return outputs[match[1]] if match else value

    needs = {job: {"result": "success"} for job in required}
    if failed_job == "windows-native":
        if scope("tests-os") in ("all", "windows"):
            needs["tests-os"]["result"] = result
        if scope("bootstrap-installer") in ("all", "windows"):
            needs["bootstrap-installer"]["result"] = result
    elif failed_job == "macos-native":
        assert scope("tests-os") in ("all", "macos")
        needs["tests-os"]["result"] = result
    elif failed_job == "posix-native":
        assert scope("bootstrap-installer") in ("all", "posix")
        needs["bootstrap-installer"]["result"] = result
    else:
        assert failed_job in needs, f"Shared/security coverage omitted: {failed_job}"
        needs[failed_job]["result"] = result

    verdict = _module(ROOT / "scripts/ci/required_results.py").evaluate_gate(needs, release=release)
    assert verdict["ok"] == (failed_job == "windows-native" and not windows_required), verdict
    if outputs:
        assert (outputs["advisory_windows"] == "true") == (not windows_required)
        advisory = {name: job for name, job in jobs.items() if job.get("with", {}).get("platform_scope") == "windows"}
        assert {job["uses"] for job in advisory.values()} == {"./.github/workflows/tests-os.yml", "./.github/workflows/bootstrap-installer.yml"}
        assert set(advisory).isdisjoint(required)
        assert all(not job.get("continue-on-error") for job in advisory.values())
        assert all("needs.detect.outputs.advisory_windows" in job["if"] for job in advisory.values())
