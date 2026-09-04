from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from plugins.hermes_revenue_lab.scripts import run_codex_effort_concurrency as effort_concurrency
from plugins.hermes_revenue_lab.scripts import run_cloud_provider_benchmarks as cloud


def _row(provider: str, model: str, task, success: bool = True) -> dict[str, object]:
    return {
        "provider": provider,
        "model": model,
        "task_id": task.task_id,
        "task_family": task.family,
        "status": "completed",
        "success": success,
        "wall_time_seconds": 1.0,
    }


def test_response_checksum_binds_tool_call_arguments() -> None:
    first = cloud.response_checksum("", {"name": "store_candidate", "arguments": {"score": 4}})
    second = cloud.response_checksum("", {"name": "store_candidate", "arguments": {"score": 5}})

    assert first != second


def test_summarize_requires_complete_corpus_before_route_selection() -> None:
    tasks = cloud.benchmark_corpus()
    document = {
        "providers": {"test": {"models": ["partial", "complete"]}},
        "records": [_row("test", "partial", task) for task in tasks[:-1]]
        + [_row("test", "complete", task) for task in tasks],
    }

    cloud.summarize(document)

    candidates = {
        row["model"]
        for selection in document["selections"].values()
        for row in selection["candidates"]
    }
    assert "complete" in candidates
    assert "partial" not in candidates


def test_seed_import_rejects_corpus_mismatch(tmp_path) -> None:
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            {
                "corpus_version": cloud.CORPUS_VERSION,
                "corpus_sha256": "0" * 64,
                "records": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="corpus mismatch"):
        cloud.load_seeded_records([seed], cloud.benchmark_corpus())


def test_run_provider_records_missing_credentials_as_environment_blocked(tmp_path) -> None:
    output = tmp_path / "receipt.json"
    document = {
        "providers": {"test": {"models": ["missing-key"], "mode": "chat"}},
        "records": [],
    }

    cloud.run_provider(
        "test",
        {"api_key": "", "models": ("missing-key",), "mode": "chat"},
        document,
        output,
        smoke=True,
        qualified=None,
    )

    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["records"][0]["status"] == "environment-blocked"
    assert receipt["records"][0]["reason_codes"] == ["provider_credentials_missing"]


def test_concurrency_runs_only_qualified_effort(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str, str]] = []

    class StubClient:
        def __init__(self, **_kwargs):
            pass

    class StubHelper:
        SYSTEM = "stub"

        @staticmethod
        def provider_specs():
            return {
                "openai-codex": {
                    "api_key": "key",
                    "base_url": "https://example.invalid",
                    "headers": {},
                }
            }

    def fake_invoke(_client, _helper, model, effort, task, phase, concurrency=None, repetition=0):
        calls.append((phase, effort, task.task_id))
        return {
            "phase": phase,
            "model": model,
            "effort": effort,
            "task_id": task.task_id,
            "task_family": task.family,
            "concurrency": concurrency,
            "repetition": repetition,
            "status": "completed",
            "success": effort == "medium",
            "reason_codes": [] if effort == "medium" else ["failed"],
            "wall_time_seconds": 0.01,
            "ended_at": cloud.now(),
        }

    monkeypatch.setattr(effort_concurrency, "load_provider_module", lambda: StubHelper)
    monkeypatch.setattr(effort_concurrency, "OpenAI", StubClient)
    monkeypatch.setattr(effort_concurrency, "invoke", fake_invoke)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_codex_effort_concurrency.py",
            "--model",
            "gpt-test",
            "--effort",
            "low",
            "--effort",
            "medium",
            "--concurrency",
            "1",
            "--output",
            str(tmp_path / "receipt.json"),
        ],
    )

    assert effort_concurrency.main() == 0

    concurrency_efforts = [effort for phase, effort, _task_id in calls if phase == "concurrency"]
    assert concurrency_efforts
    assert set(concurrency_efforts) == {"medium"}
