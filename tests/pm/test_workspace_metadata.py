"""Generated plugin workspaces preserve usable core and virtual member versions."""

import subprocess
import tomllib

from pm import workspace
from pm.environment import managed_environment
from tests.pm.test_workspace import layout as layout  # noqa: F401
from tests.pm.test_environment_build import locked_project as locked_project  # noqa: F401


def test_generated_core_version_satisfies_plugin_without_mutating_source(layout):
    tmp, core, plugin, _ = layout
    metadata = core / "pyproject.toml"
    metadata.write_text(
        metadata
        .read_text()
        .replace('name="construction-root"', 'name="hermes-agent"')
        .replace('version="1"', 'version="0.0.0"'),
        encoding="utf-8",
    )
    member = plugin / "pyproject.toml"
    member.write_text(
        member.read_text().replace(
            '"member-dep==1.0"', '"member-dep==1.0", "hermes-agent>=0.19"'
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", str(core)], check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=core, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=core,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "tag", "v0.20.4"], cwd=core, check=True)
    before = metadata.read_bytes()
    root = tmp / "workspace"
    workspace.lock_and_sync(
        [plugin],
        [],
        root=root,
        source=core,
        seed_lock=None,
        environment=managed_environment(tmp / "env"),
    )
    generated = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert generated["project"]["version"] == "0.20.4"
    assert metadata.read_bytes() == before


def test_virtual_plugin_without_declared_version_resolves_without_mutation(layout):
    tmp, core, plugin, _ = layout
    metadata = plugin / "pyproject.toml"
    metadata.write_text(
        metadata.read_text().replace('version="1"\n', ""), encoding="utf-8"
    )
    before = metadata.read_bytes()
    root = tmp / "workspace"
    workspace.lock_and_sync(
        [plugin],
        [],
        root=root,
        source=core,
        seed_lock=None,
        environment=managed_environment(tmp / "env"),
    )
    generated = next((root / "plugin-sources").glob("*/pyproject.toml"))
    assert (
        tomllib.loads(generated.read_text(encoding="utf-8"))["project"]["version"]
        == "0.0.0"
    )
    assert metadata.read_bytes() == before
