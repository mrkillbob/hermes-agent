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
