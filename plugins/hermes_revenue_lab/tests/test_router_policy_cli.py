from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from hermes_revenue_lab.routing.policy import PolicyIntegrityError
from plugins.hermes_revenue_lab.scripts.build_model_routing_policy import build_policy


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "model_benchmarks"


class RouterPolicyCliTest(unittest.TestCase):
    def test_generator_is_deterministic_and_matches_canonical_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "policy.json"
            first = build_policy(
                target,
                ARTIFACT_ROOT / "model_benchmark.json",
                ARTIFACT_ROOT / "model_selections.json",
                ARTIFACT_ROOT / "model_benchmark_checksums.sha256",
            )
            first_bytes = target.read_bytes()
            second = build_policy(
                target,
                ARTIFACT_ROOT / "model_benchmark.json",
                ARTIFACT_ROOT / "model_selections.json",
                ARTIFACT_ROOT / "model_benchmark_checksums.sha256",
            )

            self.assertEqual(first, second)
            self.assertEqual(first_bytes, target.read_bytes())
            self.assertEqual((ROOT / "config" / "model_routing_policy.json").read_bytes(), first_bytes)
            self.assertEqual(hashlib.sha256(first_bytes).hexdigest(), first)

    def test_invalid_evidence_cannot_replace_existing_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "model_benchmark.json",
                "model_benchmark.md",
                "model_selections.json",
                "model_benchmark_checksums.sha256",
            ):
                shutil.copy2(ARTIFACT_ROOT / name, root / name)
            (root / "model_selections.json").write_text("{}\n", encoding="utf-8")
            target = root / "policy.json"
            target.write_text("preserve-me\n", encoding="utf-8")

            with self.assertRaises(PolicyIntegrityError):
                build_policy(
                    target,
                    root / "model_benchmark.json",
                    root / "model_selections.json",
                    root / "model_benchmark_checksums.sha256",
                )

            self.assertEqual("preserve-me\n", target.read_text())


if __name__ == "__main__":
    unittest.main()
