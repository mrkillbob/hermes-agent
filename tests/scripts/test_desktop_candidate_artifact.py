"""Candidate export rejects trust/provenance or final-byte mismatches."""
from __future__ import annotations

import json

import pytest


def _payload(tmp_path):
    from pm.store import tree_digest

    payload = tmp_path / "payload"
    tool = payload / "tools/python-tool"
    tool.mkdir(parents=True)
    (tool / "python").write_bytes(b"synthetic signed tool bytes")
    lock = payload / "hermes-agent/pm/lock.json"
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"schema": 1, "packages": {
        "python": {"version": "3.14.7", "artifacts": {
            "darwin-arm64": {"url": "https://example.test/python", "sha256": "b" * 64},
        }},
    }}), encoding="utf-8")
    facts = payload / "tools/facts.json"
    facts.write_text(json.dumps({"schema": 1, "packages": {
        "python": {"version": "3.14.7", "entry": tool.name, "target": "darwin-arm64",
                   "artifacts": ["b" * 64], "digest": tree_digest(tool)},
    }}), encoding="utf-8")
    manifest = payload / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": 1, "target": "darwin-arm64", "repo": "hermes-agent",
        "ref": "a" * 40, "store": "tools",
        "runtime": {"repoDir": "hermes-agent"},
    }), encoding="utf-8")
    source_lock = tmp_path / "source-lock.json"
    source_lock.write_bytes(lock.read_bytes())
    return payload, tool, facts, source_lock


def test_signature_report_requires_explicit_adhoc_and_hardened_runtime():
    from scripts.bundles.desktop_candidate_artifact import verify_signature_report

    report = "CodeDirectory v=20500 flags=0x10002(adhoc,runtime)\nSignature=adhoc\nTeamIdentifier=not set\n"
    verify_signature_report(report)
    for invalid in (
        report.replace("Signature=adhoc", "Authority=Developer ID Application: Fixture"),
        report.replace("adhoc,runtime", "adhoc"),
        report.replace("TeamIdentifier=not set", "TeamIdentifier=ABC123"),
    ):
        with pytest.raises(ValueError, match="ad-hoc"):
            verify_signature_report(invalid)


def test_payload_facts_are_verified_without_repairing_or_realizing_tools(tmp_path):
    from scripts.bundles.desktop_candidate_artifact import verify_payload_facts

    payload, tool, facts, source_lock = _payload(tmp_path)
    before = facts.read_bytes()
    result = verify_payload_facts(payload, "a" * 40, source_lock)
    assert result["toolsVerified"] == 1
    assert len(result["payloadSha256"]) == 64
    assert facts.read_bytes() == before
    (tool / "python").write_bytes(b"mutation after the recorded signature")
    with pytest.raises(ValueError, match="digest"):
        verify_payload_facts(payload, "a" * 40, source_lock)
    assert facts.read_bytes() == before


@pytest.mark.parametrize("change", [
    {"entry": "../outside"}, {"artifacts": ["c" * 64]},
    {"target": "darwin-x64"}, {"version": "0.0.0"},
])
def test_payload_cannot_substitute_a_foreign_tool_record(tmp_path, change):
    from scripts.bundles.desktop_candidate_artifact import verify_payload_facts

    payload, _tool, facts, source_lock = _payload(tmp_path)
    value = json.loads(facts.read_text())
    value["packages"]["python"].update(change)
    facts.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="payload"):
        verify_payload_facts(payload, "a" * 40, source_lock)


def test_payload_source_ref_is_bound_to_the_requested_commit(tmp_path):
    from scripts.bundles.desktop_candidate_artifact import verify_payload_facts

    payload, _tool, _facts, source_lock = _payload(tmp_path)
    with pytest.raises(ValueError, match="provenance"):
        verify_payload_facts(payload, "c" * 40, source_lock)


def test_payload_lock_must_match_the_admitted_source_lock(tmp_path):
    from scripts.bundles.desktop_candidate_artifact import verify_payload_facts

    payload, _tool, _facts, source_lock = _payload(tmp_path)
    source_lock.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="lock provenance"):
        verify_payload_facts(payload, "a" * 40, source_lock)


@pytest.mark.parametrize("name,target", [
    ("../outside", None),
    ("Hermes.app/link", "../../outside"),
    ("Hermes.app/link", "../Other.app/outside"),
    ("/absolute", None),
])
def test_zip_escaping_members_are_refused_before_extraction(tmp_path, name, target):
    import stat
    import zipfile
    from scripts.bundles.desktop_candidate_artifact import inspect_zip

    package = tmp_path / "candidate.zip"
    with zipfile.ZipFile(package, "w") as archive:
        member = zipfile.ZipInfo(name)
        if target is not None:
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(member, target or "synthetic")
    with pytest.raises(ValueError, match="ZIP"):
        inspect_zip(package)


def test_zip_preserves_contained_framework_links(tmp_path):
    import stat
    import zipfile
    from scripts.bundles.desktop_candidate_artifact import inspect_zip

    package = tmp_path / "candidate.zip"
    with zipfile.ZipFile(package, "w") as archive:
        member = zipfile.ZipInfo("Hermes.app/Framework.framework/Versions/Current")
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(member, "A")
    inspect_zip(package)


@pytest.mark.parametrize("member_name", [
    "Hermes.app/link/member",
    "Hermes.app/link/../../outside",
])
@pytest.mark.parametrize("reverse", [False, True])
def test_zip_never_writes_through_an_archive_symlink(tmp_path, member_name, reverse):
    import stat
    import zipfile
    from scripts.bundles.desktop_candidate_artifact import inspect_zip

    package = tmp_path / "candidate.zip"
    link = zipfile.ZipInfo("Hermes.app/link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    entries = [(link, "."), (member_name, "synthetic")]
    if reverse:
        entries.reverse()
    with zipfile.ZipFile(package, "w") as archive:
        for member, data in entries:
            archive.writestr(member, data)
    with pytest.raises(ValueError, match="ZIP"):
        inspect_zip(package)
