#!/usr/bin/env python3
"""Separate native Windows checks from shared CI without changing runners."""

from __future__ import annotations

import argparse
import json
import os

OS_TESTS = (
    {"name": "macOS-only tests", "runner": "macos-latest", "marker": "macos"},
    {"name": "Windows-only tests", "runner": "windows-latest-32-core",
     "fork_runner": "windows-2025", "marker": "windows"},
    {"name": "Windows-only tests (arm64)", "runner": "windows-latest-32-arm-core",
     "fork_runner": "windows-11-arm", "marker": "windows", "timeout": 60},
)


def policy_scopes(repository: str, release: bool, ref_type: str) -> dict[str, str]:
    advisory = repository == "mrkillbob/hermes-agent" and not release and ref_type == "branch"
    return {
        "os_tests_scope": "macos" if advisory else "all",
        "bootstrap_tests_scope": "posix" if advisory else "all",
        "advisory_windows": str(advisory).lower(),
    }


def native_plan(workflow: str, scope: str) -> dict[str, str]:
    if workflow == "os" and scope in {"all", "macos", "windows"}:
        rows = [row for row in OS_TESTS if scope == "all" or row["marker"] == scope]
        return {"matrix": json.dumps({"include": rows}, separators=(",", ":")),
                "run_windows": str(scope != "macos").lower()}
    if workflow == "bootstrap" and scope in {"all", "posix", "windows"}:
        return {"run_posix": str(scope != "windows").lower(),
                "run_windows": str(scope != "posix").lower()}
    raise ValueError(f"Invalid native test scope: {workflow}/{scope}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    policy = commands.add_parser("policy")
    policy.add_argument("--repository", required=True)
    policy.add_argument("--release", choices=("", "false", "true"), default="false")
    policy.add_argument("--ref-type", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--workflow", choices=("os", "bootstrap"), required=True)
    plan.add_argument("--scope", required=True)
    args = parser.parse_args()
    if args.command == "policy":
        outputs = policy_scopes(args.repository, args.release == "true", args.ref_type)
    else:
        try:
            outputs = native_plan(args.workflow, args.scope)
        except ValueError as error:
            parser.error(str(error))
    lines = [f"{key}={value}" for key, value in outputs.items()]
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as destination:
            destination.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
