"""The Windows update E2E failure bundle classifies runtime metadata without exposing values."""

import json
from pathlib import Path

from tests.e2e.core.windows_update._machine import _runtime_metadata_diagnostic


def test_runtime_metadata_diagnostic_reports_parse_shape_without_values(tmp_path: Path):
    path = tmp_path / "gateway.pid"
    path.write_text(json.dumps({"pid": 4321, "start_time": 12.5, "hermes_home": "C:/private"}))

    detail = _runtime_metadata_diagnostic(path)

    assert detail == "json=dict keys=['hermes_home', 'pid', 'start_time'] unknown_keys=0"
    assert "4321" not in detail
    assert "C:/private" not in detail


def test_runtime_metadata_diagnostic_distinguishes_empty_invalid_and_missing(tmp_path: Path):
    path = tmp_path / "gateway.lock"
    assert _runtime_metadata_diagnostic(path) == "missing"

    path.write_text("  ")
    assert _runtime_metadata_diagnostic(path) == "empty"

    path.write_text("{\"pid\":")
    assert _runtime_metadata_diagnostic(path) == "invalid-json bytes=7"


def test_runtime_metadata_diagnostic_bounds_and_classifies_bytes(tmp_path: Path):
    path = tmp_path / "gateway.lock"
    path.write_bytes(b"\xff\xfe")
    assert _runtime_metadata_diagnostic(path) == "invalid-utf8 bytes=2"

    path.write_bytes(b"x" * (64 * 1024 + 1))
    assert _runtime_metadata_diagnostic(path) == "oversized bytes>65536"
