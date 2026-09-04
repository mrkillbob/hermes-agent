"""CLI bridge for the external Hermes Revenue Lab checkout."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path.home() / "HermesRevenueLab"
PROJECT_NAME = "hermes-revenue-lab"
PYTHONPATH_SUFFIX = "src:."


class RevenueLabError(RuntimeError):
    """Raised when the configured Revenue Lab checkout is not usable."""


def _resolve_root(value: str = "") -> Path:
    candidate = value or os.environ.get("HERMES_REVENUE_LAB_ROOT", "") or str(DEFAULT_ROOT)
    return Path(candidate).expanduser().resolve()


def _validate_root(root: Path) -> None:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        raise RevenueLabError(f"Hermes Revenue Lab checkout not found at {root}")
    try:
        metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise RevenueLabError(f"invalid HRL pyproject.toml: {exc}") from exc
    if metadata.get("project", {}).get("name") != PROJECT_NAME:
        raise RevenueLabError(f"{root} is not a {PROJECT_NAME} checkout")


def _run(
    root: Path,
    args: tuple[str, ...],
    *,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=root,
        check=check,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def _git_value(root: Path, args: tuple[str, ...], default: str = "") -> str:
    proc = _run(root, ("git", *args))
    return proc.stdout.strip() if proc.returncode == 0 else default


def _python(root: Path) -> str:
    hrl_python = root / ".venv" / "bin" / "python"
    if hrl_python.exists():
        return str(hrl_python)
    return sys.executable


def _subprocess_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    prefix = f"{root / 'src'}:{root}"
    env["PYTHONPATH"] = f"{prefix}:{existing}" if existing else prefix
    return env


def status_payload(root: Path) -> dict[str, Any]:
    _validate_root(root)
    runbooks = sorted((root / "docs" / "runbooks").glob("hrl-*.md"))
    scripts = root / "scripts"
    return {
        "status": "available",
        "root": str(root),
        "branch": _git_value(root, ("branch", "--show-current"), "detached"),
        "head": _git_value(root, ("rev-parse", "HEAD")),
        "dirty": bool(_git_value(root, ("status", "--porcelain"))),
        "remote": _git_value(root, ("remote", "get-url", "origin")),
        "runbook_count": len(runbooks),
        "entrypoints": {
            "guard": str(scripts / "revenue_guard.py"),
            "dashboard": str(scripts / "run_revenue_dashboard.py"),
            "provider_benchmark": str(scripts / "run_cloud_provider_benchmarks.py"),
            "effort_concurrency": str(scripts / "run_codex_effort_concurrency.py"),
        },
    }


def _cmd_status(args: argparse.Namespace) -> int:
    print(json.dumps(status_payload(_resolve_root(args.root)), indent=2, sort_keys=True))
    return 0


def _run_python_script(root: Path, script: str, extra: tuple[str, ...]) -> int:
    _validate_root(root)
    proc = subprocess.run(
        (_python(root), script, *extra),
        cwd=root,
        env=_subprocess_env(root),
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode


def _cmd_guard(args: argparse.Namespace) -> int:
    extra = ["--workload", args.workload]
    if args.parameters is not None:
        extra.extend(["--parameters", str(args.parameters)])
    if args.allowed_model:
        extra.extend(["--allowed-model", args.allowed_model])
    return _run_python_script(_resolve_root(args.root), "scripts/revenue_guard.py", tuple(extra))


def _cmd_preflight(args: argparse.Namespace) -> int:
    root = _resolve_root(args.root)
    _validate_root(root)
    tests = [
        "tests/test_model_corpus.py",
        "tests/test_model_validators.py",
    ]
    provider_tests = root / "tests" / "test_provider_benchmark_scripts.py"
    if provider_tests.exists():
        tests.append("tests/test_provider_benchmark_scripts.py")
    proc = subprocess.run(
        (_python(root), "-m", "pytest", "-q", *tests),
        cwd=root,
        env=_subprocess_env(root),
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode


def register_cli(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--root",
        default="",
        help=(
            "Hermes Revenue Lab checkout path; defaults to "
            "HERMES_REVENUE_LAB_ROOT or ~/HermesRevenueLab"
        ),
    )
    subs = subparser.add_subparsers(dest="revenue_lab_action")

    status = subs.add_parser("status", help="Show HRL checkout and entrypoint status as JSON")
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
