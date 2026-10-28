"""The external action must never install Hermes into the plugin checkout."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import hermes_yaml as yaml


def _validator_repository(repo, destination):
    """Keep real bootstrap code, without transferring unrelated application assets."""
    destination.mkdir()
    for directory in ("pm", "hermes_platform"):
        shutil.copytree(repo / directory, destination / directory,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for relative in ("hermes_constants.py", "scripts/ci/setup_toolchain.py"):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative, target)
    subprocess.run(["git", "init", "-b", "main"], cwd=destination, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=destination, check=True, capture_output=True)
    commit = ["git", "-c", "user.name=Hermes test", "-c", "user.email=test@example.invalid",
              "-c", "commit.gpgsign=false", "commit", "-m", "Validator bootstrap fixture"]
    subprocess.run(commit, cwd=destination, check=True, capture_output=True)
    requested = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=destination, text=True).strip()
    subprocess.run(["git", "tag", "requested"], cwd=destination, check=True, capture_output=True)
    (destination / "tip-only.txt").write_text("The requested revision is not the default branch tip.\n")
    subprocess.run(["git", "add", "tip-only.txt"], cwd=destination, check=True, capture_output=True)
    subprocess.run(commit, cwd=destination, check=True, capture_output=True)
    return requested


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("requested_ref", ["sha", "tag"])
def test_external_validator_checkout_uses_requested_ref_and_preserves_caller(tmp_path, requested_ref):
    repo = Path(__file__).resolve().parents[2]
    action = yaml.safe_load((repo / ".github/actions/plugin-validate/action.yml").read_text())
    source_step = next(step for step in action["runs"]["steps"] if step.get("id") == "source")
    fixture = tmp_path / "validator upstream"
    ref = _validator_repository(repo, fixture)
    caller = tmp_path / "plugin"
    caller.mkdir()
    manifest = caller / "pyproject.toml"
    manifest.write_text('[project]\nname = "caller-plugin"\nversion = "1.0.0"\n')
    before = manifest.read_bytes()
    runner = tmp_path / "runner"
    runner.mkdir()
    output = tmp_path / "output"
    env_file = tmp_path / "env"
    env = {**os.environ, "RUNNER_TEMP": str(runner), "RUNNER_OS": "Linux",
           "GITHUB_OUTPUT": str(output), "GITHUB_ENV": str(env_file), "_HERMES_REF": ref if requested_ref == "sha" else "requested",
           "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.{fixture.as_uri()}.insteadOf",
           "GIT_CONFIG_VALUE_0": "https://github.com/NousResearch/hermes-agent.git"}
    result = subprocess.run(["bash", "-c", source_step["run"]], cwd=caller, env=env, check=False,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    source = Path(outputs["source"])
    assert source.is_relative_to(runner)
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip() == ref
    assert not (source / "tip-only.txt").exists()
    assert manifest.read_bytes() == before
    assert sorted(path.name for path in caller.iterdir()) == ["pyproject.toml"]
    assert "python-version" in outputs  # the real setup-pm pin reader ran
    assert not (source / ".build/validator").exists()  # preparation is not installation
