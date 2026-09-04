from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from hermes_revenue_lab.cron.fleet import (
    CronFleetIntegrityError,
    HermesProviderBinding,
    build_hermes_create_argv,
    load_verified_cron_fleet,
    preflight_job,
)
from plugins.hermes_revenue_lab.scripts.cron_preflight import (
    parse_model_list,
    render_preflight_gate,
    verify_installed_job_definition,
)
from plugins.hermes_revenue_lab.scripts.install_cron_fleet import stage_enabled_scripts

ROOT = Path(__file__).resolve().parents[1]
FLEET_PATH = ROOT / "config" / "cron_fleet.json"
FLEET_CHECKSUM_PATH = ROOT / "config" / "cron_fleet.sha256"
POLICY_PATH = ROOT / "config" / "model_routing_policy.json"
BENCHMARK_ROOT = ROOT / "artifacts" / "model_benchmarks"


def load_fleet():
    return load_verified_cron_fleet(
        FLEET_PATH,
        FLEET_CHECKSUM_PATH,
        POLICY_PATH,
        BENCHMARK_ROOT / "model_benchmark.json",
        BENCHMARK_ROOT / "model_selections.json",
        BENCHMARK_ROOT / "model_benchmark_checksums.sha256",
    )


class CronFleetTest(unittest.TestCase):
    def test_manifest_contains_exact_patch_roles(self) -> None:
        fleet = load_fleet()
        self.assertEqual(
            {
                "frequent_deterministic_checks",
                "lightweight_opportunity_normalization",
                "daily_opportunity_report",
                "weekly_experiment_review",
                "coding_build_queue",
                "tier4_high_value_escalation",
            },
            set(fleet.jobs),
        )

    def test_deterministic_checks_are_no_agent_and_unpinned(self) -> None:
        job = load_fleet().jobs["frequent_deterministic_checks"]
        self.assertTrue(job.enabled)
        self.assertTrue(job.no_agent)
        self.assertEqual("no_llm", job.tier)
        self.assertIsNone(job.model)
        self.assertIsNone(job.provider)
        argv = build_hermes_create_argv(job, hermes_executable=Path("/opt/hermes"))
        self.assertIn("--no-agent", argv)
        self.assertEqual(
            "hrl14-frequent_deterministic_checks.py",
            argv[argv.index("--script") + 1],
        )
        self.assertNotIn("--model", argv)
        self.assertNotIn("--provider", argv)

    def test_fast_normalization_is_exactly_pinned(self) -> None:
        job = load_fleet().jobs["lightweight_opportunity_normalization"]
        self.assertTrue(job.enabled)
        self.assertFalse(job.no_agent)
        self.assertEqual("fast", job.tier)
        self.assertEqual("qwen3.5:4b", job.model)
        self.assertEqual("2a654d98e6fb", job.model_digest)
        self.assertEqual("ollama-launch", job.provider)
        self.assertEqual("none", job.reasoning_effort)
        argv = build_hermes_create_argv(job, hermes_executable=Path("/opt/hermes"))
        self.assertIn("--model", argv)
        self.assertEqual("qwen3.5:4b", argv[argv.index("--model") + 1])
        self.assertEqual("ollama-launch", argv[argv.index("--provider") + 1])

    def test_unavailable_model_tiers_are_disabled_without_fallback(self) -> None:
        fleet = load_fleet()
        for job_id in (
            "daily_opportunity_report",
            "weekly_experiment_review",
            "coding_build_queue",
        ):
            job = fleet.jobs[job_id]
            self.assertFalse(job.enabled)
            self.assertIsNone(job.model)
            self.assertIsNone(job.provider)
            self.assertEqual("selected_tier_unavailable", job.disabled_reason)
            with self.assertRaisesRegex(ValueError, "disabled cron job"):
                build_hermes_create_argv(job)

    def test_coding_job_is_outside_protected_hours_even_while_disabled(self) -> None:
        job = load_fleet().jobs["coding_build_queue"]
        self.assertEqual("0 20 * * 1-5", job.schedule)
        self.assertEqual("heavy_compile", job.workload_kind)
        self.assertTrue(job.outside_protected_hours)

    def test_tier4_has_no_schedule_and_requires_high_value_gate(self) -> None:
        job = load_fleet().jobs["tier4_high_value_escalation"]
        self.assertFalse(job.enabled)
        self.assertIsNone(job.schedule)
        self.assertEqual("manual_or_high_value_gate", job.trigger)
        self.assertTrue(job.escalation_flag_required)
        with self.assertRaisesRegex(ValueError, "disabled cron job"):
            build_hermes_create_argv(job)

    def test_preflight_accepts_only_exact_live_provider_binding(self) -> None:
        job = load_fleet().jobs["lightweight_opportunity_normalization"]
        expected = HermesProviderBinding(
            provider="ollama-launch",
            default_model="hermes-qwen3-fast",
            endpoint="http://127.0.0.1:11434/v1",
            available_models=("qwen3.5:4b",),
        )
        decision = preflight_job(job, live_binding=expected, luna_active=False)
        self.assertTrue(decision.permitted)
        self.assertEqual((), decision.reasons)

        for drifted in (
            HermesProviderBinding(
                "other",
                "hermes-qwen3-fast",
                expected.endpoint,
                expected.available_models,
            ),
            HermesProviderBinding(
                "ollama-launch", "changed", expected.endpoint, expected.available_models
            ),
            HermesProviderBinding(
                "ollama-launch",
                "hermes-qwen3-fast",
                "http://remote/v1",
                expected.available_models,
            ),
            HermesProviderBinding(
                "ollama-launch", "hermes-qwen3-fast", expected.endpoint, ("other",)
            ),
        ):
            decision = preflight_job(job, live_binding=drifted, luna_active=False)
            self.assertFalse(decision.permitted)
            self.assertIn("provider_configuration_drift", decision.reasons)

    def test_preflight_yields_model_work_to_luna(self) -> None:
        fleet = load_fleet()
        job = fleet.jobs["lightweight_opportunity_normalization"]
        binding = HermesProviderBinding(
            provider=fleet.expected_provider,
            default_model=fleet.expected_default_model,
            endpoint=fleet.expected_endpoint,
            available_models=(job.model or "",),
        )
        decision = preflight_job(job, live_binding=binding, luna_active=True)
        self.assertFalse(decision.permitted)
        self.assertIn("luna_active", decision.reasons)

    def test_manifest_checksum_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "cron_fleet.json"
            checksum = root / "cron_fleet.sha256"
            manifest.write_bytes(FLEET_PATH.read_bytes() + b" ")
            checksum.write_text(
                FLEET_CHECKSUM_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )
            with self.assertRaisesRegex(CronFleetIntegrityError, "checksum"):
                load_verified_cron_fleet(
                    manifest,
                    checksum,
                    POLICY_PATH,
                    BENCHMARK_ROOT / "model_benchmark.json",
                    BENCHMARK_ROOT / "model_selections.json",
                    BENCHMARK_ROOT / "model_benchmark_checksums.sha256",
                )

    def test_policy_checksum_drift_fails_closed(self) -> None:
        document = json.loads(FLEET_PATH.read_text(encoding="utf-8"))
        document["routing_policy_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "cron_fleet.json"
            checksum = root / "cron_fleet.sha256"
            payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
            manifest.write_bytes(payload)
            checksum.write_text(
                f"{hashlib.sha256(payload).hexdigest()}  cron_fleet.json\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                CronFleetIntegrityError, "routing policy checksum"
            ):
                load_verified_cron_fleet(
                    manifest,
                    checksum,
                    POLICY_PATH,
                    BENCHMARK_ROOT / "model_benchmark.json",
                    BENCHMARK_ROOT / "model_selections.json",
                    BENCHMARK_ROOT / "model_benchmark_checksums.sha256",
                )

    def test_provider_model_list_parser_is_exact_and_bounded(self) -> None:
        self.assertEqual(
            ("qwen3.5:4b", "qwen3-coder:30b"),
            parse_model_list("- qwen3.5:4b\n- qwen3-coder:30b\n"),
        )
        with self.assertRaisesRegex(ValueError, "model list"):
            parse_model_list("qwen3.5:4b\n")

    def test_model_preflight_block_uses_hermes_wake_gate(self) -> None:
        fleet = load_fleet()
        job = fleet.jobs["lightweight_opportunity_normalization"]
        rendered = json.loads(
            render_preflight_gate(
                job,
                live_binding=HermesProviderBinding(
                    "changed-provider",
                    fleet.expected_default_model,
                    fleet.expected_endpoint,
                    (job.model or "",),
                ),
                luna_active=False,
                resource_permitted=True,
                resource_reasons=(),
                opportunity_context=({"candidate_id": "candidate-1"},),
            )
        )
        self.assertEqual({"wakeAgent": False}, rendered)

    def test_fast_job_stays_silent_when_there_is_no_work(self) -> None:
        fleet = load_fleet()
        job = fleet.jobs["lightweight_opportunity_normalization"]
        rendered = json.loads(
            render_preflight_gate(
                job,
                live_binding=HermesProviderBinding(
                    fleet.expected_provider,
                    fleet.expected_default_model,
                    fleet.expected_endpoint,
                    (job.model or "",),
                ),
                luna_active=False,
                resource_permitted=True,
                resource_reasons=(),
                opportunity_context=(),
            )
        )
        self.assertEqual({"wakeAgent": False}, rendered)

    def test_installer_stages_only_enabled_jobs_with_private_executable_mode(
        self,
    ) -> None:
        fleet = load_fleet()
        with tempfile.TemporaryDirectory() as directory:
            scripts_dir = Path(directory) / "scripts"
            staged = stage_enabled_scripts(
                fleet,
                source=ROOT / "scripts" / "cron_preflight.py",
                scripts_dir=scripts_dir,
            )
            self.assertEqual(
                {
                    "hrl14-frequent_deterministic_checks.py",
                    "hrl14-lightweight_opportunity_normalization.py",
                },
                {path.name for path in staged},
            )
            for path in staged:
                self.assertEqual(
                    (ROOT / "scripts" / "cron_preflight.py").read_bytes(),
                    path.read_bytes(),
                )
                self.assertEqual(0o700, path.stat().st_mode & 0o777)

    def test_installed_job_definition_must_retain_every_pin(self) -> None:
        job = load_fleet().jobs["lightweight_opportunity_normalization"]
        exact = {
            "id": "abc123def456",
            "enabled": True,
            "deliver": "local",
            "model": "qwen3.5:4b",
            "provider": "ollama-launch",
            "reasoning_effort": "none",
            "no_agent": False,
            "script": job.script_name,
            "workdir": job.workdir,
            "schedule": {"kind": "cron", "expr": job.schedule},
        }
        verify_installed_job_definition(job, {"jobs": [exact]})
        for field, value in (
            ("model", "other"),
            ("provider", "other"),
            ("reasoning_effort", "high"),
            ("enabled", False),
        ):
            drifted = {**exact, field: value}
            with self.assertRaisesRegex(ValueError, "definition drift"):
                verify_installed_job_definition(job, {"jobs": [drifted]})


if __name__ == "__main__":
    unittest.main()
