from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from hermes_revenue_lab.provenance import (
    ArtifactRunStore,
    ModelUsage,
    RunManifest,
    RunVerdict,
    SourceRecord,
    verify_run,
)

STARTED = "2026-08-21T18:00:00+00:00"
ENDED = "2026-08-21T18:00:05+00:00"
SHA_A = "a" * 64
SHA_B = "b" * 64


def manifest(**overrides: object) -> RunManifest:
    values = {
        "run_id": "run-20260821-001",
        "experiment_id": "data-monitor-001",
        "task_name": "normalize_public_records",
        "run_reason": "Scheduled bounded opportunity normalization.",
        "started_at": STARTED,
        "ended_at": ENDED,
        "code_commit": "f1f495b",
        "routing_policy_sha256": SHA_A,
        "compliance_registry_sha256": SHA_B,
        "approval_id": None,
    }
    values.update(overrides)
    return RunManifest(**values)


def source(**overrides: object) -> SourceRecord:
    values = {
        "source_id": "county-feed-001",
        "locator": "https://example.gov/data/feed.json",
        "source_kind": "public_api",
        "collected_at": STARTED,
        "content_sha256": SHA_A,
        "permission_basis": "public_api_terms",
        "license_status": "permitted",
        "terms_status": "permitted",
        "robots_status": "not_applicable",
    }
    values.update(overrides)
    return SourceRecord(**values)


def usage(**overrides: object) -> ModelUsage:
    values = {
        "usage_id": "model-call-001",
        "requested_tier": "fast",
        "actual_tier": "fast",
        "actual_model": "qwen3.5:4b",
        "model_digest": "2a654d98e6fb",
        "provider": "ollama-launch",
        "escalation_reason": None,
        "started_at": STARTED,
        "ended_at": ENDED,
        "input_tokens": 200,
        "output_tokens": 50,
        "cost_status": "known",
        "estimated_cost_usd": Decimal(0),
    }
    values.update(overrides)
    return ModelUsage(**values)


def verdict(**overrides: object) -> RunVerdict:
    values = {
        "run_id": "run-20260821-001",
        "status": "completed",
        "experiment_decision": "continue",
        "reason_codes": ("bounded_normalization_complete",),
        "cost_status": "known",
        "total_cost_usd": Decimal(0),
        "revenue_status": "known",
        "gross_revenue_usd": Decimal(0),
        "revenue_ledger_ref": "ledger:data-monitor-001",
        "output_summary": "One normalized private draft was produced.",
    }
    values.update(overrides)
    return RunVerdict(**values)


class ProvenanceTypeTest(unittest.TestCase):
    def test_model_usage_requires_exact_identity_or_explicit_no_llm(self) -> None:
        with self.assertRaisesRegex(ValueError, "model identity"):
            usage(actual_model=None)
        no_llm = usage(
            requested_tier="no_llm",
            actual_tier="no_llm",
            actual_model=None,
            model_digest=None,
            provider=None,
            input_tokens=None,
            output_tokens=None,
        )
        self.assertIsNone(no_llm.actual_model)
        with self.assertRaisesRegex(ValueError, "tier change reason"):
            usage(actual_tier="standard")

    def test_unknown_cost_and_revenue_are_not_coerced_to_zero(self) -> None:
        unknown_usage = usage(cost_status="unknown", estimated_cost_usd=None)
        unknown_verdict = verdict(
            cost_status="unknown",
            total_cost_usd=None,
            revenue_status="unknown",
            gross_revenue_usd=None,
            revenue_ledger_ref=None,
        )
        self.assertIsNone(unknown_usage.estimated_cost_usd)
        self.assertIsNone(unknown_verdict.gross_revenue_usd)
        with self.assertRaisesRegex(ValueError, "unknown cost"):
            usage(cost_status="unknown", estimated_cost_usd=Decimal(0))
        with self.assertRaisesRegex(ValueError, "completed run"):
            verdict(status="failed", experiment_decision="promote")

    def test_source_records_reject_credentials_and_invalid_digests(self) -> None:
        with self.assertRaisesRegex(ValueError, "public HTTP"):
            source(locator="https://user:password@example.gov/feed")
        with self.assertRaisesRegex(ValueError, "digest"):
            source(content_sha256="not-a-digest")


class ArtifactRunStoreTest(unittest.TestCase):
    def test_complete_run_is_atomic_private_and_independently_verifiable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactRunStore(root / "artifacts", allowed_root=root)
            run_dir = store.write_run(
                manifest=manifest(),
                inputs={"candidate_ids": ["candidate-001"], "raw_customer_data": False},
                sources=(source(),),
                model_usage=(usage(),),
                outputs={"normalized.json": {"candidate_id": "candidate-001"}},
                logs={"run.log": "normalization completed\n"},
                verdict=verdict(),
            )

            self.assertEqual(0o700, run_dir.stat().st_mode & 0o777)
            self.assertEqual(
                {
                    "checksums.sha256",
                    "inputs.json",
                    "logs",
                    "manifest.json",
                    "model_usage.json",
                    "outputs",
                    "sources.json",
                    "verdict.json",
                },
                {path.name for path in run_dir.iterdir()},
            )
            self.assertEqual(0o600, (run_dir / "manifest.json").stat().st_mode & 0o777)
            result = verify_run(run_dir, allowed_root=root)
            self.assertTrue(result.valid, result.reasons)
            document = json.loads((run_dir / "manifest.json").read_text())
            self.assertEqual("5", document["duration_seconds"])

    def test_existing_run_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactRunStore(root / "artifacts", allowed_root=root)
            arguments = {
                "manifest": manifest(),
                "inputs": {},
                "sources": (source(),),
                "model_usage": (usage(),),
                "outputs": {"result.json": {}},
                "logs": {},
                "verdict": verdict(),
            }
            store.write_run(**arguments)
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                store.write_run(**arguments)

    def test_unknown_model_cost_forces_unknown_total_cost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactRunStore(root / "artifacts", allowed_root=root)
            with self.assertRaisesRegex(ValueError, "total cost must remain unknown"):
                store.write_run(
                    manifest=manifest(),
                    inputs={},
                    sources=(),
                    model_usage=(
                        usage(cost_status="unknown", estimated_cost_usd=None),
                    ),
                    outputs={},
                    logs={},
                    verdict=verdict(),
                )

    def test_unpermitted_source_forces_a_blocked_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactRunStore(root / "artifacts", allowed_root=root)
            unknown = source(license_status="unknown")
            with self.assertRaisesRegex(ValueError, "blocked verdict"):
                store.write_run(
                    manifest=manifest(),
                    inputs={},
                    sources=(unknown,),
                    model_usage=(),
                    outputs={},
                    logs={},
                    verdict=verdict(),
                )
            blocked = verdict(
                status="blocked",
                experiment_decision="block",
                reason_codes=("source_permission_unknown",),
            )
            run_dir = store.write_run(
                manifest=manifest(run_id="run-20260821-002"),
                inputs={},
                sources=(replace(unknown, source_id="unknown-source-002"),),
                model_usage=(),
                outputs={},
                logs={},
                verdict=replace(blocked, run_id="run-20260821-002"),
            )
            self.assertTrue(verify_run(run_dir, allowed_root=root).valid)

    def test_tampering_and_untracked_files_fail_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactRunStore(root / "artifacts", allowed_root=root)
            run_dir = store.write_run(
                manifest=manifest(),
                inputs={},
                sources=(source(),),
                model_usage=(),
                outputs={"result.json": {}},
                logs={},
                verdict=verdict(),
            )
            (run_dir / "outputs" / "result.json").write_text('{"changed":true}\n')
            result = verify_run(run_dir, allowed_root=root)
            self.assertFalse(result.valid)
            self.assertIn("checksum mismatch: outputs/result.json", result.reasons)
            (run_dir / "untracked.txt").write_text("surprise")
            self.assertIn(
                "untracked artifact: untracked.txt",
                verify_run(run_dir, allowed_root=root).reasons,
            )
            (run_dir / "untracked-directory").mkdir()
            self.assertIn(
                "untracked artifact directory: untracked-directory",
                verify_run(run_dir, allowed_root=root).reasons,
            )

    def test_unsafe_artifact_permissions_fail_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = ArtifactRunStore(root / "artifacts", allowed_root=root).write_run(
                manifest=manifest(),
                inputs={},
                sources=(),
                model_usage=(),
                outputs={},
                logs={},
                verdict=verdict(),
            )
            (run_dir / "manifest.json").chmod(0o644)
            self.assertIn(
                "unsafe artifact mode: manifest.json",
                verify_run(run_dir, allowed_root=root).reasons,
            )

    def test_operator_cli_returns_machine_readable_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = ArtifactRunStore(root / "artifacts", allowed_root=root).write_run(
                manifest=manifest(),
                inputs={},
                sources=(),
                model_usage=(),
                outputs={},
                logs={},
                verdict=verdict(),
            )
            completed = subprocess.run(
                (
                    str(
                        Path(__file__).parents[1] / "scripts" / "verify_run_artifact.py"
                    ),
                    str(run_dir),
                    "--allowed-root",
                    str(root),
                ),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(
                {"reasons": [], "run_id": manifest().run_id, "valid": True},
                json.loads(completed.stdout),
            )

    def test_paths_are_root_contained_and_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            with self.assertRaisesRegex(ValueError, "outside the allowed root"):
                ArtifactRunStore(outside / "artifacts", allowed_root=root)
            symlink = root / "linked-artifacts"
            symlink.symlink_to(root / "actual-artifacts", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinks"):
                ArtifactRunStore(symlink, allowed_root=root)
            store = ArtifactRunStore(root / "artifacts", allowed_root=root)
            with self.assertRaisesRegex(ValueError, "relative artifact path"):
                store.write_run(
                    manifest=manifest(),
                    inputs={},
                    sources=(),
                    model_usage=(),
                    outputs={"../escape.json": {}},
                    logs={},
                    verdict=verdict(),
                )

    def test_secret_labeled_inputs_are_rejected_without_a_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ArtifactRunStore(root / "artifacts", allowed_root=root)
            with self.assertRaisesRegex(ValueError, "sensitive key"):
                store.write_run(
                    manifest=manifest(),
                    inputs={"api_key": "must-not-persist"},
                    sources=(),
                    model_usage=(),
                    outputs={},
                    logs={},
                    verdict=verdict(),
                )
            self.assertFalse((root / "artifacts" / "runs" / manifest().run_id).exists())


if __name__ == "__main__":
    unittest.main()
