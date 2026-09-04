#!/usr/bin/env python3
"""Plan or install only the enabled, checksum-verified HRL-14 Hermes jobs."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parents[1]
HERMES_AGENT_ROOT = LAB_ROOT.parents[1]
sys.path[:0] = [str(HERMES_AGENT_ROOT), str(LAB_ROOT / "src")]

from hermes_constants import get_hermes_home
from hermes_revenue_lab.cron.fleet import (
    CronFleet,
    build_hermes_create_argv,
    load_verified_cron_fleet,
)
from hermes_revenue_lab.inventory.parsers import parse_hermes_cron

HERMES = Path(
    os.environ.get("HRL_HERMES_BIN") or shutil.which("hermes") or (Path.home() / ".local" / "bin" / "hermes")
)
SCRIPTS_DIR = get_hermes_home() / "scripts"
BENCHMARK_ROOT = LAB_ROOT / "artifacts" / "model_benchmarks"
_CREATED = re.compile(r"Created job:\s*([0-9a-f]{6,64})")


def _fleet() -> CronFleet:
    return load_verified_cron_fleet(
        LAB_ROOT / "config" / "cron_fleet.json",
        LAB_ROOT / "config" / "cron_fleet.sha256",
        LAB_ROOT / "config" / "model_routing_policy.json",
        BENCHMARK_ROOT / "model_benchmark.json",
        BENCHMARK_ROOT / "model_selections.json",
        BENCHMARK_ROOT / "model_benchmark_checksums.sha256",
    )


def _write_private_executable(destination: Path, payload: bytes) -> None:
    if destination.is_symlink():
        raise ValueError(f"refusing symlink script destination {destination.name}")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    try:
        position = 0
        while position < len(payload):
            written = os.write(descriptor, payload[position:])
            if written <= 0:
                raise OSError("script staging write did not advance")
            position += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, destination)
    destination.chmod(0o700)


def stage_enabled_scripts(
    fleet: CronFleet,
    *,
    source: Path,
    scripts_dir: Path,
    replace: bool = False,
) -> tuple[Path, ...]:
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 1_048_576:
        raise ValueError("cron preflight source is not a bounded regular file")
    if scripts_dir.is_symlink():
        raise ValueError("Hermes scripts directory cannot be a symlink")
    scripts_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    scripts_dir.chmod(0o700)
    payload = source.read_bytes()
    staged: list[Path] = []
    for job in fleet.jobs.values():
        if not job.enabled:
            continue
        destination = scripts_dir / job.script_name
        if destination.exists() and destination.read_bytes() != payload and not replace:
            raise ValueError(f"existing Hermes script differs: {destination.name}")
        _write_private_executable(destination, payload)
        staged.append(destination)
    return tuple(staged)


def _run(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        check=False,
    )


def _existing_job_names() -> set[str]:
    result = _run((str(HERMES), "cron", "list", "--all"))
    if result.returncode != 0:
        raise RuntimeError("Hermes cron inventory is unavailable")
    return {row["name"] for row in parse_hermes_cron(result.stdout) if "name" in row}


def install(fleet: CronFleet) -> tuple[str, ...]:
    enabled = tuple(job for job in fleet.jobs.values() if job.enabled)
    existing = _existing_job_names()
    duplicates = sorted(job.name for job in enabled if job.name in existing)
    if duplicates:
        raise RuntimeError(
            f"refusing unverified existing cron jobs: {', '.join(duplicates)}"
        )
    stage_enabled_scripts(
        fleet,
        source=LAB_ROOT / "scripts" / "cron_preflight.py",
        scripts_dir=SCRIPTS_DIR,
    )
    created: list[str] = []
    try:
        for job in enabled:
            result = _run(build_hermes_create_argv(job))
            match = _CREATED.search(result.stdout)
            if result.returncode != 0 or match is None:
                raise RuntimeError(f"Hermes did not confirm creation of {job.job_id}")
            created.append(match.group(1))
    except Exception:
        for job_id in reversed(created):
            _run((str(HERMES), "cron", "remove", job_id))
        raise
    return tuple(created)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install", action="store_true")
    mode.add_argument("--refresh-scripts", action="store_true")
    args = parser.parse_args(argv)
    fleet = _fleet()
    enabled = tuple(job for job in fleet.jobs.values() if job.enabled)
    if args.refresh_scripts:
        staged = stage_enabled_scripts(
            fleet,
            source=LAB_ROOT / "scripts" / "cron_preflight.py",
            scripts_dir=SCRIPTS_DIR,
            replace=True,
        )
        print(
            json.dumps(
                {
                    "mode": "scripts_refreshed",
                    "scripts": [path.name for path in staged],
                },
                sort_keys=True,
            )
        )
        return 0
    if not args.install:
        print(
            json.dumps(
                {
                    "commands": [
                        list(build_hermes_create_argv(job)) for job in enabled
                    ],
                    "disabled_jobs": sorted(
                        job.job_id for job in fleet.jobs.values() if not job.enabled
                    ),
                    "mode": "plan_only",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    created = install(fleet)
    print(
        json.dumps(
            {"created_job_ids": list(created), "mode": "installed"}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
