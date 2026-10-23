"""Make plugin sources importable under the repository's hermetic test runner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def pytest_configure(config):
    # Plugin tests are outside tests/, so its isolation conftest never runs.
    # Clear task identity before collection: handoff tests spawn real CLI children.
    import os
    import tempfile
    sandbox = tempfile.TemporaryDirectory(prefix="hermes-feedback-tests-")
    patch = pytest.MonkeyPatch()
    for name in tuple(os.environ):
        if name.startswith("HERMES_KANBAN_") or name == "HERMES_PROFILE":
            patch.delenv(name, raising=False)
    patch.setenv("HOME", sandbox.name)
    patch.setenv("HERMES_HOME", str(Path(sandbox.name) / ".hermes"))
    patch.setenv("HERMES_TEST_ISOLATION", str(Path(sandbox.name) / ".hermes"))
    config.add_cleanup(sandbox.cleanup)
    config.add_cleanup(patch.undo)
    config.addinivalue_line("markers", "platforms(*specs, arch=None, arch_negate=False): native platform gating")


def pytest_collection_modifyitems(config, items):
    from tests._fixtures.platform_gating import _platforms_gate_reason, _reject_contradictory_platform_marks
    _reject_contradictory_platform_marks(items)
    for item in items:
        reason = _platforms_gate_reason(item)
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))
    host = sys.platform
    os_marks = {
        "windows_only": (host == "win32", "native Windows"),
        "macos_only": (host == "darwin", "macOS"),
        "linux_only": (host.startswith("linux"), "Linux"),
    }
    for mark_name, (is_host, label) in os_marks.items():
        if is_host:
            continue
        skip = pytest.mark.skip(
            reason=f"{label}-only test (marked {mark_name}); host is {host}"
        )
        for item in items:
            if item.get_closest_marker(mark_name) is not None:
                item.add_marker(skip)


@pytest.fixture
def retirement_linked(tmp_path):
    import os
    from retirement_fixtures import git
    source, workspace = tmp_path / "source", tmp_path / "slot"
    source.mkdir()
    git(source, "init", "--quiet")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.invalid")
    git(source, "config", "core.hooksPath", os.devnull)
    git(source, "remote", "add", "origin", "https://github.com/fixture/repo.git")
    (source / "tracked").write_text("base\n", encoding="utf-8")
    (source / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
    git(source, "add", ".")
    git(source, "commit", "--quiet", "-m", "fixture")
    git(source, "worktree", "add", "--quiet", "-b", "occupant", str(workspace))
    return source, workspace
