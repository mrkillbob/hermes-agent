"""Run plugin capacity regressions in the canonical generic and native test lanes."""

from pathlib import Path
import subprocess
import sys

import pytest

from tools.environments.local import build_subprocess_env


def _run_capacity_suite():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-v",
            "plugins/github-pr-feedback/tests/test_worktree_capacity.py",
        ],
        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90,
        env=build_subprocess_env(
            inherit_profile_home=False,
            extra={"HERMES_DISABLE_LAZY_INSTALLS": "1"},
        ),
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    # Explicit evidence that the native junction/symlink and process fence
    # branches were executed, rather than an empty/filtered collection.
    for name in (
        "test_actual_symlink_ancestor_is_protected",
        "test_real_processes_case_unicode_alias_share_single_fence",
        "test_pooled_capacity_rejection_cannot_escape_into_overflow",
    ):
        assert f"{name} PASSED" in output, output


def test_feedback_capacity_suite_in_generic_ci():
    _run_capacity_suite()


@pytest.mark.platforms("windows", "macos")
def test_feedback_capacity_suite_on_native_hosts():
    _run_capacity_suite()
