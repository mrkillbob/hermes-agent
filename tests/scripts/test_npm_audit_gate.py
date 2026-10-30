"""Fork-only regression (see FORK_PATCHES.md): the audit gate excuses only allow-listed advisories."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "npm_audit_gate", Path(__file__).resolve().parents[2] / "scripts" / "ci" / "npm_audit_gate.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _report(*advisories):
    return {"vulnerabilities": {a[0]: {"via": [{"name": a[0], "title": "t", "severity": a[1],
                                                "url": f"https://github.com/advisories/{a[2]}"}]}
                                for a in advisories}}


def test_unlisted_high_advisory_is_blocking():
    found = gate.blocking_advisories(_report(("pkg", "high", "GHSA-new-new-new")))
    assert set(found) - gate.ALLOWED == {"GHSA-new-new-new"}


def test_allow_listed_and_low_severity_do_not_block():
    allowed = next(iter(gate.ALLOWED))
    found = gate.blocking_advisories(_report(("braces", "high", allowed), ("x", "moderate", "GHSA-mod")))
    assert set(found) - gate.ALLOWED == set()
