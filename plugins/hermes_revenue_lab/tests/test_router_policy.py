from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from hermes_revenue_lab.routing.policy import (
    PolicyIntegrityError,
    derive_policy_document,
    load_verified_policy,
)
from hermes_revenue_lab.routing.types import RoutingPolicy


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "model_benchmarks"


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


class RouterPolicyTest(unittest.TestCase):
    def test_derivation_preserves_only_verified_selection_and_fixed_controls(self) -> None:
        selections = json.loads((ARTIFACT_ROOT / "model_selections.json").read_text())
        digest = hashlib.sha256(canonical_json(selections)).hexdigest()

        document = derive_policy_document(selections, selections_sha256=digest)

        self.assertEqual("hrl.model_routing_policy.v1", document["schema_version"])
        self.assertEqual(
            {"no_llm", "fast", "standard", "reasoning", "coding", "escalation"},
            set(document["tiers"]),
        )
        self.assertEqual("qwen3.5:4b", document["tiers"]["fast"]["model"])
        self.assertEqual("2a654d98e6fb", document["tiers"]["fast"]["model_digest"])
        self.assertFalse(document["tiers"]["fast"]["thinking"])
        self.assertFalse(document["tiers"]["fast"]["permitted_during_luna"])
        self.assertEqual("unavailable", document["tiers"]["coding"]["status"])
        self.assertIsNone(document["tiers"]["coding"]["model"])
        self.assertTrue(document["tiers"]["no_llm"]["permitted_during_luna"])

    def test_canonical_policy_loads_only_when_all_bindings_verify(self) -> None:
        policy = load_verified_policy(
            ROOT / "config" / "model_routing_policy.json",
            ARTIFACT_ROOT / "model_benchmark.json",
            ARTIFACT_ROOT / "model_selections.json",
            ARTIFACT_ROOT / "model_benchmark_checksums.sha256",
        )

        self.assertEqual("qwen3.5:4b", policy.tiers["fast"].model)
        self.assertEqual("unavailable", policy.tiers["coding"].status)

    def test_benchmark_checksum_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "model_benchmark.json",
                "model_selections.json",
                "model_benchmark.md",
                "model_benchmark_checksums.sha256",
            ):
                shutil.copy2(ARTIFACT_ROOT / name, root / name)
            (root / "model_benchmark.json").write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(PolicyIntegrityError, "checksum mismatch"):
                load_verified_policy(
                    ROOT / "config" / "model_routing_policy.json",
                    root / "model_benchmark.json",
                    root / "model_selections.json",
                    root / "model_benchmark_checksums.sha256",
                )

    def test_hand_edited_policy_model_fails_derivation_check(self) -> None:
        canonical = json.loads((ROOT / "config" / "model_routing_policy.json").read_text())
        drifted = copy.deepcopy(canonical)
        drifted["tiers"]["fast"]["model"] = "qwen3-coder:30b"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_bytes(canonical_json(drifted))

            with self.assertRaisesRegex(PolicyIntegrityError, "derived policy"):
                load_verified_policy(
                    path,
                    ARTIFACT_ROOT / "model_benchmark.json",
                    ARTIFACT_ROOT / "model_selections.json",
                    ARTIFACT_ROOT / "model_benchmark_checksums.sha256",
                )

    def test_unavailable_tier_cannot_contain_a_model(self) -> None:
        selections = json.loads((ARTIFACT_ROOT / "model_selections.json").read_text())
        document = derive_policy_document(
            selections,
            selections_sha256=hashlib.sha256(canonical_json(selections)).hexdigest(),
        )
        document["tiers"]["coding"]["model"] = "qwen3-coder:30b"

        with self.assertRaisesRegex(ValueError, "unavailable tier coding"):
            RoutingPolicy.from_document(document)

    def test_derivation_rejects_model_in_unavailable_selection(self) -> None:
        selections = json.loads((ARTIFACT_ROOT / "model_selections.json").read_text())
        selections["tiers"]["coding"]["model"] = "qwen3-coder:30b"
        selections["tiers"]["coding"]["model_digest"] = "06c1097efce0"

        with self.assertRaisesRegex(PolicyIntegrityError, "unavailable selection tier coding"):
            derive_policy_document(selections, selections_sha256="a" * 64)

    def test_policy_derivation_is_byte_deterministic(self) -> None:
        selections = json.loads((ARTIFACT_ROOT / "model_selections.json").read_text())
        digest = hashlib.sha256(canonical_json(selections)).hexdigest()
        first = canonical_json(derive_policy_document(selections, selections_sha256=digest))
        second = canonical_json(derive_policy_document(selections, selections_sha256=digest))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
