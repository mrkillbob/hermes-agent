"""Exercise the classifier bootstrap shell against transient API failures."""

import base64
import os
from pathlib import Path
import subprocess

import pytest
from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[2]
PATHS = (".github/actions/detect-changes/action.yml", "scripts/ci/classify_changes.py")


@pytest.mark.parametrize("recover", [True, False])
def test_classifier_fetch_retries_without_decoding_failed_responses(tmp_path, recover):
    steps = YAML(typ="base").load((ROOT / ".github/workflows/ci.yaml").read_text())[
        "jobs"
    ]["detect"]["steps"]
    script = steps[0]["run"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    encoded = base64.b64encode(b"classifier contents\n").decode()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/bash\n"
        'echo "$*" >> "$CALLS"\n'
        'if [ ! -f "$FAILED" ] || [ "$RECOVER" = false ]; then\n'
        '  touch "$FAILED"\n'
        '  echo "API error response"\n'
        '  echo "gh: HTTP 429" >&2\n'
        "  exit 1\n"
        "fi\n"
        f"printf '%s\\n' '{encoded}'\n"
    )
    gh.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/bin/bash\nexit 0\n")
    sleep.chmod(0o755)
    calls = tmp_path / "calls"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "FAILED": str(tmp_path / "failed"),
        "RECOVER": str(recover).lower(),
        "CLASSIFIER_REPOSITORY": "owner/repo",
        "CLASSIFIER_REF": "a" * 40,
    }
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    requests = calls.read_text().splitlines()
    assert all(f"?ref={'a' * 40}" in request for request in requests)
    assert "base64:" not in result.stderr
    if recover:
        assert result.returncode == 0, result.stderr
        assert len(requests) == 3
        for path in PATHS:
            assert (tmp_path / path).read_bytes() == b"classifier contents\n"
    else:
        assert result.returncode != 0
        assert len(requests) == 3
        assert not any((tmp_path / path).exists() for path in PATHS)
