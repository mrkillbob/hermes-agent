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
    """Keep the global age rule and bound the temporary Brotlicffi exception.

    The cutoff admits only the reviewed release and artifacts. Removing the
    exception restores the uniform three-day rule.
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
    assert exception == {"brotlicffi": "2026-10-22T16:19:34Z"}
    pins = [req for req in map(Requirement, manifest["project"]["optional-dependencies"]["messaging"])
            if req.name == "brotlicffi"]
    assert len(pins) == 1 and str(pins[0].specifier) == "==1.2.1.0"
    releases = [row for row in lock["package"] if row["name"] == "brotlicffi"]
    assert len(releases) == 1 and releases[0]["version"] == "1.2.1.0"
    artifacts = [releases[0]["sdist"], *releases[0]["wheels"]]
    assert artifacts and {artifact["hash"] for artifact in artifacts} <= {
        "sha256:fea6f5450a6bd73ec222e92282d4db25030943946190697c1f2cd41087c59bc6",
        "sha256:b77704eb33e345e00d0a2854abf7966b86e1709a1f89bcc143b6c8437256d7c7",
        "sha256:0cb591da62db91dddd701a28def82f39e0f7943321ac7d0814a1d2a1625095d1",
        "sha256:6f5ff7c8f62eaa3e6feb2b4d06a6001bb08cc346c9de7608786de99387255e94",
        "sha256:421ba24ee6568c6baa3c83e0f584fe9e3941bf86401122942c5ed085f8114c09",
        "sha256:cc882e1ab3f81d262cc002132476dab7667f8f41ead3de8609b163cb64409f8a",
        "sha256:ed7c8311209853c9d78e2f6cff1f57e4df2541e4d67e3677dc1f4c88539db907",
        "sha256:0a7281a39de91432e90c27ab9c4f9feb3a9cf33fda3873534c2a88270d9f60a2",
        "sha256:25ab19c47b88438b5d00a2074b86fe37ab5c176aef90de1ae0f5e1ae52300462",
        "sha256:00a6e8df24c2f48744bbdca8694a2b50bb26d1d0b6360c3a7c5caeaf4ad83373",
        "sha256:54ccae9a0ff09c7f007aa247dc62b6ca7666ecf1358568e6f2eb8683ceed1d01",
        "sha256:1563024a25dd2c357457f49f4e03d4354d0660ae41ed1983d86c5817a754a539",
        "sha256:ac2d63c8f3c1fd000286315027a013008c7c9a3009c7415c24ee48dde1b207db",
        "sha256:2182d9af39ff06efce62a783d9429bf01f10cdedafc8b6ba696f75b52fc39eef",
        "sha256:75e54388f06cd7edf5354f526d0386553db441b7574a22a2bcb19a2149e48eb7",
        "sha256:30a52a620564ef3c6a34cd1e09d699b92ba68a3447a28915b77a17eb5d2c8f71",
        "sha256:602e87ecc8fd2c72b13ba6dced2e0d5aefc18a262523e82795ed0f47f5a13903",
        "sha256:52b99120543ffa3b0667e922c6a7dc6b6e01350f7fd38f21aaca511bb9f8b4f2",
        "sha256:5d0b89e959f643aab4c58c15c06e1e87b6f6605e5e88c3c1c28f5ce1c58c4e5f",
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
