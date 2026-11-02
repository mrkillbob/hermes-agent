"""Native sort option contracts: GNU executes a leading-dash program; uutils rejects it."""

import os
import shutil
import subprocess

import pytest

from tools.approval import detect_dangerous_command


def _find_sort(implementation, candidates):
    observed = []
    for name in candidates:
        binary = shutil.which(name)
        if binary is None:
            continue
        version = subprocess.run(
            [binary, "--version"], text=True, capture_output=True, timeout=5, check=False
        )
        observed.append((binary, version.returncode, version.stdout, version.stderr))
        if version.returncode == 0 and implementation in version.stdout:
            return binary, observed
    return None, observed


@pytest.fixture
def sort_payload(tmp_path):
    marker = tmp_path / "executed"
    payload = tmp_path / "-payload-marker"
    payload.write_text('#!/bin/sh\nprintf executed > "$MARKER"\ncat\n')
    payload.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "MARKER": str(marker)}
    bulk = "\n".join(str(number) for number in range(10_000, 0, -1)) + "\n"
    return marker, env, bulk


@pytest.mark.platforms("linux", "macos")
def test_gnu_sort_executes_separate_leading_dash_program(sort_payload):
    # Ubuntu exposes GNU utilities as gnu-prefixed commands; Homebrew uses gsort.
    binary, observed = _find_sort("GNU coreutils", ("gnusort", "gsort", "sort"))
    assert binary is not None, f"GNU sort is required for this execution contract: {observed!r}"
    marker, env, bulk = sort_payload
    completed = subprocess.run(
        [binary, "--buffer-size=1K", "--compress-program", "-payload-marker"],
        input=bulk, text=True, capture_output=True, env=env, timeout=20, check=False,
    )
    assert completed.returncode == 0, (observed, completed.returncode, completed.stderr)
    assert marker.exists(), (observed, completed.stderr)
    assert marker.read_text() == "executed"
    assert detect_dangerous_command("sort --compress-program -payload-marker names")[0]


@pytest.mark.platforms("linux")
def test_uutils_sort_rejects_separate_leading_dash_program(sort_payload):
    binary, observed = _find_sort("uutils coreutils", ("sort",))
    if binary is None:
        pytest.skip(f"uutils sort is not the installed sort: {observed!r}")
    marker, env, bulk = sort_payload
    completed = subprocess.run(
        [binary, "--buffer-size=1K", "--compress-program", "-payload-marker"],
        input=bulk, text=True, capture_output=True, env=env, timeout=20, check=False,
    )
    assert completed.returncode == 2, (observed, completed.returncode, completed.stderr)
    assert "unexpected argument '-p'" in completed.stderr
    assert not marker.exists()
    # Approval remains conservative across installed sort implementations.
    assert detect_dangerous_command("sort --compress-program -payload-marker names")[0]
