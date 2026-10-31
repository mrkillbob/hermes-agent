"""Development signing is an explicit prepared commit-build selection."""
from __future__ import annotations

import pytest

from scripts.bundles.desktop_prepare import BuildRequest


def _data(tmp_path):
    return {
        "source": str(tmp_path), "work": str(tmp_path / "work"),
        "cache": str(tmp_path / "cache"), "commit": "a" * 40, "tag": None,
        "version": "1.2.3", "variant": "bundled", "target": "darwin-arm64",
        "bundle_env": {}, "channel_request": None, "release_epoch": None,
    }


def test_legacy_preparation_does_not_opt_into_development_signing(tmp_path):
    request = BuildRequest.from_data(_data(tmp_path))
    assert getattr(request, "development_candidate", None) is False


def test_explicit_candidate_survives_the_request_digest_roundtrip(tmp_path):
    data = {**_data(tmp_path), "development_candidate": True}
    try:
        candidate = BuildRequest.from_data(data)
    except TypeError:
        pytest.fail("prepared requests cannot yet admit explicit development signing")
    assert candidate.data() == data
    normal = BuildRequest.from_data({**data, "development_candidate": False})
    assert candidate.identity_digest() != normal.identity_digest()


@pytest.mark.parametrize("change", [
    {"variant": "light"}, {"variant": "store"}, {"target": "darwin-x64"},
    {"target": "linux-arm64"}, {"tag": "v1.2.3"}, {"channel_request": {}},
    {"release_epoch": 123}, {"commit": "HEAD"},
    {"development_candidate": "true"},
])
def test_candidate_refuses_release_and_foreign_target_records(tmp_path, change):
    data = {**_data(tmp_path), "development_candidate": True, **change}
    with pytest.raises(ValueError, match="development candidate"):
        BuildRequest.from_data(data)


@pytest.mark.parametrize("key", [
    "CSC_LINK", "CSC_NAME", "CSC_KEY_PASSWORD", "CSC_KEYCHAIN",
    "APPLE_API_KEY", "APPLE_NOTARY_PROFILE", "HERMES_MAC_SIGNATURE_CACHE",
    "HERMES_PREPARED_PACKAGING",
])
def test_candidate_rejects_ambient_signing_inputs_before_preparation(tmp_path, monkeypatch, key):
    monkeypatch.setenv(key, "synthetic")
    with pytest.raises(ValueError, match="development candidate refuses ambient"):
        BuildRequest.from_data({**_data(tmp_path), "development_candidate": True})
    # The candidate policy does not change normal release input handling.
    assert BuildRequest.from_data(_data(tmp_path)).development_candidate is False


def test_candidate_packager_binding_requires_the_complete_request(tmp_path):
    import json
    from scripts.bundles.desktop_candidate import bind_packaging, validate_packaging

    request = BuildRequest.from_data({**_data(tmp_path), "development_candidate": True})
    packager = tmp_path / "packager.json"
    packager.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="development candidate packaging"):
        validate_packaging(request, packager)
    bind_packaging(request, packager)
    validate_packaging(request, packager)
    data = json.loads(packager.read_text())
    assert data["desktopCandidate"]["requestDigest"] == request.identity_digest()
    data["desktopCandidate"]["request"]["version"] = "9.9.9"
    packager.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="development candidate packaging"):
        validate_packaging(request, packager)


def test_normal_preparation_cannot_consume_candidate_packaging(tmp_path):
    from scripts.bundles.desktop_candidate import bind_packaging, validate_packaging

    candidate = BuildRequest.from_data({**_data(tmp_path), "development_candidate": True})
    normal = BuildRequest.from_data(_data(tmp_path))
    packager = tmp_path / "packager.json"
    packager.write_text("{}", encoding="utf-8")
    bind_packaging(candidate, packager)
    with pytest.raises(ValueError, match="not admitted"):
        validate_packaging(normal, packager)


def test_candidate_does_not_restore_a_signing_home_or_accept_builder_overrides(tmp_path):
    from scripts.bundles.desktop_inputs import packaging_environment, validate_builder_identity

    request = BuildRequest.from_data({**_data(tmp_path), "development_candidate": True})
    isolated = {"HOME": str(tmp_path / "job-home")}
    inherited = {"HOME": str(tmp_path / "login-home")}
    assert packaging_environment(isolated, inherited, request.target,
                                 development_candidate=True) == isolated
    assert packaging_environment(isolated, inherited, request.target)["HOME"] == inherited["HOME"]
    validate_builder_identity(request, [])
    with pytest.raises(ValueError, match="development candidate accepts no builder overrides"):
        validate_builder_identity(request, ["-c.mac.identity=other"])


def test_candidate_cannot_use_a_legacy_missing_preparation_digest(tmp_path):
    from scripts.bundles.desktop_prepare import PreparedDesktop

    request = BuildRequest.from_data({**_data(tmp_path), "development_candidate": True})
    prepared = PreparedDesktop(
        request=request, python=tmp_path / "python", node=tmp_path / "node",
        icon_python=tmp_path / "icons/bin/python", native=tmp_path / "native",
        packager=tmp_path / "packager.json", payload=None, digests={},
        native_toolchain="fixture", request_digest=None,
    )
    with pytest.raises(ValueError, match="request identity changed"):
        prepared.validate()


@pytest.mark.platforms("macos", arch="arm64")
def test_cli_persists_candidate_selection_before_any_supplier_runs(tmp_path, monkeypatch):
    import subprocess
    import sys
    from scripts.bundles import desktop
    from scripts.bundles import desktop_prepare

    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="1.2.3"\n', encoding="utf-8")
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "-c", "core.hooksPath=/dev/null", "commit", "-m", "fixture"],
                   cwd=source, check=True, capture_output=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    admitted = []

    def prepare(request, **_kwargs):
        admitted.append(request)
        return tmp_path / "prepared.json"

    monkeypatch.setattr(desktop_prepare, "prepare", prepare)
    monkeypatch.setattr(sys, "argv", [
        "desktop.py", "--repo", str(source), "--commit", commit,
        "--development-candidate", "--prepare-only",
        "--work", str(tmp_path / "work"), "--cache", str(tmp_path / "cache"),
    ])
    desktop.main()
    assert len(admitted) == 1
    assert admitted[0].development_candidate is True
    assert admitted[0].commit == commit
