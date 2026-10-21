"""Independent core/optional dependency and reviewed CVE policies."""
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_test_dependencies_are_group_only_in_manifest_and_lock():
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    hermes = next(package for package in lock["package"] if package["name"] == manifest["project"]["name"])
    assert manifest["tool"]["uv"]["default-groups"] == []
    assert "dev" in manifest["dependency-groups"]
    assert "dev" not in manifest["project"]["optional-dependencies"]
    assert "dev" in hermes["dev-dependencies"]
    assert "dev" not in hermes.get("optional-dependencies", {})


def test_core_and_optional_speech_dependencies():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    core = {Requirement(dep).name for dep in project["dependencies"]}
    assert "packaging" in core  # Runtime code imports it directly, not transitively.
    assert "faster-whisper" not in core
    assert "faster-whisper" in {
        Requirement(dep).name for dep in project["optional-dependencies"]["stt-whisper"]
    }


def test_starlette_server_pins_and_lock_exclude_cve_2026_48710():
    # BadHost's reviewed fixed boundary is independent of today's exact pin.
    floor = Version("1.0.1")
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    found = set()
    for extra, specs in metadata["project"]["optional-dependencies"].items():
        for requirement in map(Requirement, specs):
            if requirement.name != "starlette":
                continue
            pins = list(requirement.specifier)
            assert len(pins) == 1 and pins[0].operator == "==", (extra, requirement)
            assert Version(pins[0].version) >= floor, (extra, requirement)
            found.add(extra)
    assert {"web", "mcp", "computer-use"} <= found
    dev = [req for req in map(Requirement, metadata["dependency-groups"]["dev"])
           if req.name == "starlette"]
    assert len(dev) == 1
    pins = list(dev[0].specifier)
    assert len(pins) == 1 and pins[0].operator == "==" and Version(pins[0].version) >= floor
    versions = [Version(row["version"]) for row in lock["package"] if row["name"] == "starlette"]
    assert versions and all(version >= floor for version in versions)


def test_every_dependency_is_held_to_one_three_day_release_age():
    """Keep the global age rule and bound the owner's temporary certifi exception.

    The exception admits only the reviewed release and artifacts, never other packages
    or future certifi versions. Removing it restores the uniform three-day rule.
    """
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    uv = manifest["tool"]["uv"]
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert uv["exclude-newer"] == "3 days"
    assert lock["options"].get("exclude-newer-span") == "P3D"
    exception = uv.get("exclude-newer-package", {})
    assert lock["options"].get("exclude-newer-package", {}) == exception
    if not exception:
        return
    assert exception == {"certifi": "2026-10-20T02:24:17Z"}
    pins = [req for req in map(Requirement, manifest["project"]["dependencies"])
            if req.name == "certifi"]
    assert len(pins) == 1 and str(pins[0].specifier) == "==2026.10.19"
    releases = [row for row in lock["package"] if row["name"] == "certifi"]
    assert len(releases) == 1 and releases[0]["version"] == "2026.10.19"
    artifacts = [releases[0]["sdist"], *releases[0]["wheels"]]
    assert len(artifacts) == 2
    assert {artifact["hash"] for artifact in artifacts} == {
        "sha256:42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b",
        "sha256:035c1e85572b141bf1c3a3eff0e33a8eb74917522f2559075ff8d2fea780a1ae",
    }


def test_httpx2_pins_and_lock_exclude_ghsa_7mj9_2mp8_4m2p_family():
    # 2.12.0 is the first httpx2/httpcore2 release clear of GHSA-7mj9-2mp8-4m2p,
    # GHSA-f2fp-rgf2-35cp, GHSA-h4x7-gw46-3wm6, GHSA-pf96-p4fj-6566 and
    # GHSA-8xx6-hgc6-gc2m; the boundary is independent of today's exact pin.
    floor = Version("2.12.0")
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    declared = dict(metadata["project"]["optional-dependencies"])
    declared["group:dev"] = metadata["dependency-groups"]["dev"]
    found = set()
    for where, specs in declared.items():
        for requirement in map(Requirement, specs):
            if requirement.name != "httpx2":
                continue
            pins = list(requirement.specifier)
            assert len(pins) == 1 and pins[0].operator == "==", (where, requirement)
            assert Version(pins[0].version) >= floor, (where, requirement)
            found.add(where)
    assert {"mcp", "computer-use", "group:dev"} <= found
    for name in ("httpx2", "httpcore2"):
        versions = [Version(row["version"]) for row in lock["package"] if row["name"] == name]
        assert versions and all(version >= floor for version in versions), (name, versions)
