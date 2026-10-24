"""Changes to the automatic cleanup pause closure require upgrade coverage."""
import pytest

from scripts.ci.classify_changes import classify


@pytest.mark.parametrize("path", [
    "agent/estop.py", "agent/file_safety.py", "agent/jiter_preload.py", "hermes_constants.py",
])
def test_pause_cleanup_dependency_starts_upgrade_tests(path):
    assert classify([path])["e2e_upgrade"]
