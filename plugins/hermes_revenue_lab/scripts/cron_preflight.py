#!/usr/bin/env python3
"""Run the HRL-14 policy, provider, Luna, and resource wake gate."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parents[1]
HERMES_AGENT_ROOT = LAB_ROOT.parents[1]
sys.path[:0] = [str(HERMES_AGENT_ROOT), str(LAB_ROOT / "src")]

from hermes_revenue_lab.cron.fleet import (
    CronJob,
    HermesProviderBinding,
    load_verified_cron_fleet,
    preflight_job,
)
from hermes_revenue_lab.guard.collector import collect_revenue_snapshot
from hermes_revenue_lab.guard.policy import (
    WorkloadSpec,
    evaluate_revenue_guard,
)
from hermes_revenue_lab.inventory.redaction import assert_publication_safe

HERMES = Path(
    os.environ.get("HRL_HERMES_BIN") or shutil.which("hermes") or (Path.home() / ".local" / "bin" / "hermes")
)
BENCHMARK_ROOT = LAB_ROOT / "artifacts" / "model_benchmarks"
_Runner = Callable[[Sequence[str]], str]


def _run_stdout(argv: Sequence[str]) -> str:
    completed = subprocess.run(
        tuple(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=8,
        check=False,
    )
    if completed.returncode != 0 or len(completed.stdout) > 16_384:
        raise RuntimeError("Hermes configuration evidence is unavailable")
    return completed.stdout.strip()


def parse_model_list(value: str) -> tuple[str, ...]:
    if not value or len(value) > 8_192:
        raise ValueError("Hermes provider model list is invalid")
    rows: list[str] = []
    for line in value.splitlines():
        if not line.startswith("- "):
            raise ValueError("Hermes provider model list is invalid")
        model = line[2:].strip()
        if (
            not model
            or len(model) > 128
            or any(character.isspace() for character in model)
        ):
            raise ValueError("Hermes provider model list is invalid")
        rows.append(model)
    if not rows or len(rows) != len(set(rows)):
        raise ValueError("Hermes provider model list is invalid")
    return tuple(rows)


def collect_live_provider_binding(
    *, runner: _Runner = _run_stdout
) -> HermesProviderBinding:
    prefix = (str(HERMES), "config", "get")
    return HermesProviderBinding(
        provider=runner((*prefix, "model.provider")),
        default_model=runner((*prefix, "model.default")),
        endpoint=runner((*prefix, "providers.ollama-launch.api")),
        available_models=parse_model_list(
            runner((*prefix, "providers.ollama-launch.models"))
        ),
    )


def verify_installed_job_definition(job: CronJob, document: object) -> None:
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), list):
        raise TypeError("installed cron definition evidence is invalid")
    matches = [
        value
        for value in document["jobs"]
        if isinstance(value, dict) and value.get("script") == job.script_name
    ]
    if len(matches) != 1:
        raise ValueError("installed cron definition drift detected")
    installed = matches[0]
    schedule = installed.get("schedule")
    expected = {
        "deliver": job.deliver,
        "enabled": True,
        "model": job.model,
        "no_agent": job.no_agent,
        "provider": job.provider,
        "reasoning_effort": job.reasoning_effort,
        "script": job.script_name,
        "workdir": job.workdir,
    }
    if any(installed.get(field) != value for field, value in expected.items()):
        raise ValueError("installed cron definition drift detected")
    if (
        not isinstance(schedule, dict)
        or schedule.get("kind") != "cron"
        or schedule.get("expr") != job.schedule
    ):
        raise ValueError("installed cron definition drift detected")


def _installed_jobs_document(path: Path) -> object:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
        raise ValueError("installed cron definition evidence is unavailable")
    return json.loads(path.read_text(encoding="utf-8"))


def _opportunity_context(database: Path) -> tuple[dict[str, object], ...]:
    if not database.exists():
        return ()
    if (
        database.is_symlink()
        or not database.is_file()
        or database.stat().st_size > 64 * 1024 * 1024
    ):
        raise ValueError("scout database is not a bounded regular file")
    uri = f"file:{database.resolve(strict=True)}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=2.0) as connection:
        rows = connection.execute(
            """
            SELECT candidate_id, scout_kind, subject
            FROM scout_candidates
            WHERE eligible = 1
            ORDER BY candidate_id
            LIMIT 25
            """
        ).fetchall()
    context = tuple(
        {"candidate_id": str(row[0]), "scout_kind": str(row[1]), "subject": str(row[2])}
        for row in rows
    )
    assert_publication_safe({"opportunities": list(context)})
    return context


def render_preflight_gate(
    job: CronJob,
    *,
    live_binding: HermesProviderBinding,
    luna_active: bool,
    resource_permitted: bool,
    resource_reasons: Sequence[str],
    opportunity_context: Sequence[dict[str, object]],
) -> str:
    decision = preflight_job(
        job,
        live_binding=live_binding,
        luna_active=luna_active,
        resource_permitted=resource_permitted,
        resource_reasons=resource_reasons,
    )
    if not decision.permitted:
        payload = decision.wake_gate(no_agent=job.no_agent)
    elif job.no_agent or not opportunity_context:
        payload = {"wakeAgent": False}
    else:
        context = {
            "actual_model_required": job.model,
            "job_id": job.job_id,
            "model_digest": job.model_digest,
            "opportunities": list(opportunity_context),
            "preflight": "permitted",
            "provider_required": job.provider,
        }
        assert_publication_safe(context)
        payload = {"context": context, "wakeAgent": True}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _fleet():
    return load_verified_cron_fleet(
        LAB_ROOT / "config" / "cron_fleet.json",
        LAB_ROOT / "config" / "cron_fleet.sha256",
        LAB_ROOT / "config" / "model_routing_policy.json",
        BENCHMARK_ROOT / "model_benchmark.json",
        BENCHMARK_ROOT / "model_selections.json",
        BENCHMARK_ROOT / "model_benchmark_checksums.sha256",
    )


def _job_id_from_program() -> str | None:
    name = Path(sys.argv[0]).name
    if name.startswith("hrl14-") and name.endswith(".py"):
        return name[len("hrl14-") : -len(".py")]
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", default=_job_id_from_program())
    args = parser.parse_args(argv)
    no_agent = args.job_id == "frequent_deterministic_checks"
    try:
        fleet = _fleet()
        if args.job_id not in fleet.jobs:
            raise ValueError("unknown cron fleet job")
        job = fleet.jobs[args.job_id]
        from hermes_constants import get_hermes_home

        verify_installed_job_definition(
            job,
            _installed_jobs_document(get_hermes_home() / "cron" / "jobs.json"),
        )
        if job.model is None:
            binding = HermesProviderBinding(
                fleet.expected_provider,
                fleet.expected_default_model,
                fleet.expected_endpoint,
                (),
            )
        else:
            binding = collect_live_provider_binding()
        snapshot = collect_revenue_snapshot(allowed_model=job.model)
        workload = WorkloadSpec(job.workload_kind, job.model_parameters_billions)
        guard = evaluate_revenue_guard(snapshot, workload)
        opportunities = (
            _opportunity_context(LAB_ROOT / ".hermes" / "scouts" / "scouts.db")
            if job.job_id == "lightweight_opportunity_normalization"
            else ()
        )
        print(
            render_preflight_gate(
                job,
                live_binding=binding,
                luna_active=bool(snapshot.luna_process_count),
                resource_permitted=guard.permitted,
                resource_reasons=guard.reasons,
                opportunity_context=opportunities,
            )
        )
        return 0
    # Any unanticipated preflight failure must still emit a successful wake gate. Hermes injects a
    # non-zero script error into the agent prompt, which would violate this fail-closed boundary.
    except Exception:  # noqa: BLE001
        if no_agent:
            print(
                json.dumps(
                    {
                        "context": {
                            "preflight": "blocked",
                            "reason_codes": ["preflight_error"],
                        },
                        "wakeAgent": True,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        else:
            print('{"wakeAgent":false}')
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
