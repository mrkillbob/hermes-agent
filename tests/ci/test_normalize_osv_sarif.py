"""Preserve findings while collapsing repeated OSV alias-group emissions.

The fixture is the original v2.3.8 main-run reporter artifact (39411538567).
Its complete result objects repeat twice per advisory, before GitHub upload.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci/normalize_osv_sarif.py"
_SPEC = importlib.util.spec_from_file_location("normalize_osv_sarif", _SCRIPT)
assert _SPEC and _SPEC.loader
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)
_FIXTURE = Path(__file__).parent / "fixtures/osv_alias_duplicates.sarif"


def test_original_report_keeps_all_ten_advisories_and_metadata():
    original = json.loads(_FIXTURE.read_text())
    before = copy.deepcopy(original)
    report, removed = _mod.normalize_report(original)
    assert len(report["runs"][0]["results"]) == 10
    assert removed == 10
    assert {r["ruleId"] for r in report["runs"][0]["results"]} == {
        "CVE-2026-9353", "CVE-2026-9366", "CVE-2026-9368", "CVE-2026-9369",
        "CVE-2026-53869", "CVE-2026-53870", "CVE-2026-10221", "CVE-2026-10222",
        "CVE-2026-10223", "CVE-2026-10224",
    }
    original_results = original["runs"][0]["results"]
    for finding in report["runs"][0]["results"]:
        assert original_results.count(finding) == 2
    report_without_results = copy.deepcopy(report)
    original_without_results = copy.deepcopy(original)
    for document in (report_without_results, original_without_results):
        document["runs"][0].pop("results")
    assert report_without_results == original_without_results
    assert original == before  # original artifact is never mutated
    assert _mod.normalize_report(report) == (report, 0)


@pytest.mark.parametrize("field,value", [
    ("locations", [{"physicalLocation": {"artifactLocation": {"uri": "other/uv.lock"}}}]),
    ("message", {"text": "Package hermes-agent@1.0.0 is vulnerable to CVE-2026-9353"}),
    ("level", "error"),
    ("codeFlows", [{"message": {"text": "distinct call path"}}]),
    ("properties", {"ecosystem": "distinct ecosystem", "reachable": True}),
    ("futureEvidence", {"preserve": "all fields"}),
])
def test_same_rule_and_fingerprint_does_not_hide_distinct_evidence(field, value):
    original = json.loads(_FIXTURE.read_text())
    first = original["runs"][0]["results"][0]
    other = copy.deepcopy(first)
    other[field] = value
    original["runs"][0]["results"] = [first, other, copy.deepcopy(first)]
    report, removed = _mod.normalize_report(original)
    assert report["runs"][0]["results"] == [first, other]
    assert removed == 1


def test_runs_and_empty_reports_are_preserved():
    original = json.loads(_FIXTURE.read_text())
    original["runs"].append(copy.deepcopy(original["runs"][0]))
    report, removed = _mod.normalize_report(original)
    assert [len(r["results"]) for r in report["runs"]] == [10, 10]
    assert removed == 20
    original["runs"][0]["results"] = []
    original["runs"][1].pop("results")
    assert _mod.normalize_report(original) == (original, 0)


@pytest.mark.parametrize("document", [
    None, [], {}, {"version": "2.0.0", "runs": []},
    {"version": "2.1.0", "runs": {}},
    {"version": "2.1.0", "runs": [None]},
    {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "other-tool"}}}]},
])
def test_invalid_or_non_osv_document_is_rejected(document):
    with pytest.raises(ValueError):
        _mod.normalize_report(document)


@pytest.mark.parametrize("contents", ['{"version":"2.1.0","runs":NaN}', '{"runs":[],"runs":[]}'])
def test_ambiguous_json_fails_without_creating_output(tmp_path, contents):
    source, output = tmp_path / "source.sarif", tmp_path / "out.sarif"
    source.write_text(contents)
    result = subprocess.run([sys.executable, str(_SCRIPT), str(source), str(output)], check=False, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert not output.exists()
    assert source.read_text() == contents


@pytest.mark.parametrize("bom", [False, True])
def test_cli_preserves_original_and_does_not_overwrite_existing_output(tmp_path, bom):
    source, output = tmp_path / "source.sarif", tmp_path / "out.sarif"
    original_bytes = (b"\xef\xbb\xbf" if bom else b"") + _FIXTURE.read_bytes()
    source.write_bytes(original_bytes)
    result = subprocess.run([sys.executable, str(_SCRIPT), str(source), str(output)], check=False, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "10 exact duplicates removed" in result.stdout
    assert len(json.loads(output.read_text())["runs"][0]["results"]) == 10
    assert source.read_bytes() == original_bytes
    output.write_text("existing evidence")
    result = subprocess.run([sys.executable, str(_SCRIPT), str(source), str(output)], check=False, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert output.read_text() == "existing evidence"


@pytest.mark.parametrize("results", [None, {}, [None], [{}], ["text"]])
def test_malformed_results_are_rejected(results):
    document = json.loads(_FIXTURE.read_text())
    document["runs"][0]["results"] = results
    with pytest.raises(ValueError):
        _mod.normalize_report(document)


def test_source_cannot_be_used_as_output(tmp_path):
    source = tmp_path / "source.sarif"
    source.write_bytes(_FIXTURE.read_bytes())
    result = subprocess.run([sys.executable, str(_SCRIPT), str(source), str(source)],
                            check=False, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert source.read_bytes() == _FIXTURE.read_bytes()
