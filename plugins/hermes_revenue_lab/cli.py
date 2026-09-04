"""CLI bridge for the in-tree Hermes Revenue Lab plugin.

Historically Hermes Revenue Lab (HRL) lived in its own repository
(``mrkillbob/HermesRevenueLab``) and this plugin only *pointed at* an
external checkout via ``HERMES_REVENUE_LAB_ROOT`` / ``--root``. The owner
decided HRL doesn't need to stay separate, so its code now lives in-tree at
``plugins/hermes_revenue_lab/`` (this directory's ``src/``, ``scripts/``,
``tests/``, ``docs/`` — see ``README.md`` / ``AGENTS.md`` alongside this
file for HRL's own operating rules).

The commands below (``status`` / ``preflight`` / ``guard``) are unchanged in
shape from the external-checkout era, but now resolve everything relative
to this plugin's own directory instead of an external root — no env var, no
``config.yaml`` key, no separate checkout to keep in sync.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent


class RevenueLabError(RuntimeError):
    """Raised when the in-tree Revenue Lab plugin is not usable."""


def _validate_root(root: Path) -> None:
    if not (root / "src" / "hermes_revenue_lab").is_dir():
        raise RevenueLabError(
            f"Hermes Revenue Lab source not found under {root} "
            "(expected src/hermes_revenue_lab/)"
        )


def _run(root: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=root, check=False, text=True, capture_output=True)


def _git_value(root: Path, args: tuple[str, ...], default: str = "") -> str:
    # HRL's own history was folded into the hermes-agent repo's history at
    # merge time, so these report the *hermes-agent* repo's git state (this
    # plugin no longer has an independent checkout/branch/remote of its own).
    proc = _run(root, ("git", *args))
    return proc.stdout.strip() if proc.returncode == 0 else default


def status_payload(root: Path = PLUGIN_ROOT) -> dict[str, Any]:
    _validate_root(root)
    runbooks = sorted((root / "docs" / "runbooks").glob("hrl-*.md"))
    scripts = root / "scripts"
    return {
        "status": "available",
        "mode": "in-tree",
        "root": str(root),
        "repo_branch": _git_value(root, ("branch", "--show-current"), "detached"),
        "repo_head": _git_value(root, ("rev-parse", "HEAD")),
        "repo_dirty": bool(_git_value(root, ("status", "--porcelain", "--", str(root)))),
        "runbook_count": len(runbooks),
        "entrypoints": {
            "guard": str(scripts / "revenue_guard.py"),
            "dashboard": str(scripts / "run_revenue_dashboard.py"),
            "provider_benchmark": str(scripts / "run_cloud_provider_benchmarks.py"),
            "effort_concurrency": str(scripts / "run_codex_effort_concurrency.py"),
        },
    }


def _cmd_status(args: argparse.Namespace) -> int:
    print(json.dumps(status_payload(), indent=2, sort_keys=True))
    return 0


def _subprocess_env() -> dict[str, str]:
    import os

    env = os.environ.copy()
    # HRL's own modules import as top-level ``hermes_revenue_lab.*`` (see
    # src/hermes_revenue_lab/), and its scripts import hermes-agent modules
    # (``agent.*``, ``hermes_cli.*``) directly — so both this plugin's
    # ``src/`` *and* the hermes-agent repo root need to be importable.
    repo_root = PLUGIN_ROOT.parents[1]
    prefix = f"{PLUGIN_ROOT / 'src'}:{repo_root}"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{prefix}:{existing}" if existing else prefix
    return env


def _run_python_script(script: str, extra: tuple[str, ...]) -> int:
    _validate_root(PLUGIN_ROOT)
    proc = subprocess.run(
        (sys.executable, str(PLUGIN_ROOT / script), *extra),
        cwd=PLUGIN_ROOT,
        env=_subprocess_env(),
        text=True,
    )
    return proc.returncode


def _cmd_guard(args: argparse.Namespace) -> int:
    extra = ["--workload", args.workload]
    if args.parameters is not None:
        extra.extend(["--parameters", str(args.parameters)])
    if args.allowed_model:
        extra.extend(["--allowed-model", args.allowed_model])
    return _run_python_script("scripts/revenue_guard.py", tuple(extra))


def _cmd_preflight(args: argparse.Namespace) -> int:
    _validate_root(PLUGIN_ROOT)
    tests = [
        "tests/test_model_corpus.py",
        "tests/test_model_validators.py",
    ]
    provider_tests = PLUGIN_ROOT / "tests" / "test_provider_benchmark_scripts.py"
    if provider_tests.exists():
        tests.append("tests/test_provider_benchmark_scripts.py")
    proc = subprocess.run(
        (sys.executable, "-m", "pytest", "-q", *tests),
        cwd=PLUGIN_ROOT,
        env=_subprocess_env(),
        text=True,
    )
    return proc.returncode


def register_cli(subparser: argparse.ArgumentParser) -> None:
    subs = subparser.add_subparsers(dest="revenue_lab_action")

    status = subs.add_parser("status", help="Show in-tree HRL status and entrypoints as JSON")
    status.set_defaults(func=_cmd_status)

    preflight = subs.add_parser("preflight", help="Run deterministic HRL corpus/validator tests")
    preflight.set_defaults(func=_cmd_preflight)

    guard = subs.add_parser("guard", help="Evaluate the HRL revenue guard without starting work")
    guard.add_argument(
        "--workload",
        required=True,
        choices=(
            "guard_check",
            "deterministic",
            "fast_model",
            "heavy_model",
            "image_video",
            "heavy_compile",
            "browser_swarm",
        ),
    )
    guard.add_argument("--parameters", type=float)
    guard.add_argument("--allowed-model", default="")
    guard.set_defaults(func=_cmd_guard)

    subparser.set_defaults(func=revenue_lab_command)


def revenue_lab_command(args: argparse.Namespace) -> int:
    handler = getattr(args, "func", None)
    if handler is None or handler is revenue_lab_command:
        print("Usage: hermes revenue-lab {status|preflight|guard}")
        return 2
    try:
        return int(handler(args))
    except RevenueLabError as exc:
        print(json.dumps({"status": "unavailable", "error": str(exc)}, sort_keys=True))
        return 1
