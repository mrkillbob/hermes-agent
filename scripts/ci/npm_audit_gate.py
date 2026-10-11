#!/usr/bin/env python3
"""``npm audit --audit-level=high`` that tolerates explicitly allow-listed advisories.

Usage: npm_audit_gate.py <dir>. Fails on any high/critical advisory whose GHSA id is not in
ALLOWED. Every entry needs a reason and a removal condition; drop it once a fix exists.
"""
import json
import subprocess
import sys

ALLOWED = {
    # braces <= 3.0.3 stack-exhaustion DoS: no patched release exists on npm (3.0.3 is latest),
    # so the transitive Docusaurus build-tool dependency cannot be fixed. Remove when braces > 3.0.3.
    "GHSA-vfj7-8cjw-p6xm",
}
BLOCKING = {"high", "critical"}


def blocking_advisories(report: dict) -> dict[str, str]:
    found: dict[str, str] = {}
    for vuln in (report.get("vulnerabilities") or {}).values():
        for via in vuln.get("via", []):
            if isinstance(via, dict) and via.get("severity") in BLOCKING:
                ghsa = str(via.get("url", "")).rsplit("/", 1)[-1]
                found[ghsa] = f"{via.get('name')}: {via.get('title')}"
    return found


def main(directory: str) -> int:
    cmd = ["npm", "audit", "--json"]
    kwargs = dict(cwd=directory, capture_output=True, encoding="utf-8", errors="replace")
    proc = subprocess.run(cmd, **kwargs, check=False)
    report = json.loads(proc.stdout or "{}")
    if "vulnerabilities" not in report:
        print(f"::error::npm audit produced no report: {proc.stdout[:500]}{proc.stderr[:500]}")
        return 1
    found = blocking_advisories(report)
    unexpected = {k: v for k, v in found.items() if k not in ALLOWED}
    for ghsa in sorted(set(found) & ALLOWED):
        print(f"allow-listed: {ghsa} ({found[ghsa]})")
    for ghsa, desc in sorted(unexpected.items()):
        print(f"::error::unallowed high/critical advisory {ghsa}: {desc}")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
