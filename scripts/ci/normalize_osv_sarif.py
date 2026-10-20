"""Collapse only identical OSV SARIF results within each run.

OSV v2.3.8 maps alias IDs to one finding but emits that finding per ID:
https://github.com/google/osv-scanner/blob/v2.3.8/internal/output/sarif.go#L259-L269
Keep every field in the identity, including future/unknown result fields.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


def normalize_report(report: Any) -> tuple[dict, int]:
    if not isinstance(report, dict) or report.get("version") != "2.1.0" or not isinstance(report.get("runs"), list):
        raise ValueError("expected a SARIF 2.1.0 document with runs")
    normalized = copy.deepcopy(report)
    removed = 0
    for run in normalized["runs"]:
        if not isinstance(run, dict):
            raise ValueError("expected a SARIF run object")
        tool = run.get("tool")
        driver = tool.get("driver") if isinstance(tool, dict) else None
        if not isinstance(driver, dict) or driver.get("name") != "osv-scanner":
            raise ValueError("expected the osv-scanner driver")
        if "results" not in run:
            continue
        if not isinstance(run["results"], list):
            raise ValueError("expected a SARIF results array")
        seen: set[str] = set()
        retained = []
        for finding in run["results"]:
            if not isinstance(finding, dict) or not isinstance(finding.get("ruleId"), str) or not finding["ruleId"]:
                raise ValueError("expected an OSV result object with ruleId")
            identity = json.dumps(finding, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if identity in seen:
                removed += 1
            else:
                seen.add(identity)
                retained.append(finding)
        run["results"] = retained
    return normalized, removed


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object member")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        original = json.loads(args.source.read_text(encoding="utf-8-sig"),
                              object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        normalized, removed = normalize_report(original)
        encoded = json.dumps(normalized, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
        # Never overwrite the original or an earlier output, including symlinks.
        with args.output.open("x", encoding="utf-8") as output:
            output.write(encoded)
    except (OSError, ValueError, TypeError) as exc:
        print(f"OSV SARIF normalization failed: {exc}", file=sys.stderr)
        return 1
    count = sum(len(run.get("results", [])) for run in normalized["runs"])
    print(f"OSV SARIF: {count} results retained; {removed} exact duplicates removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
