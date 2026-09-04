from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from hermes_revenue_lab.inventory.redaction import PublicationSafetyError
from hermes_revenue_lab.models.publish import publish_model_benchmark


def document() -> dict[str, object]:
    return {
        "schema_version": "hrl.model_benchmark.v1",
        "benchmark_id": "benchmark-1",
        "inventory_id": "inventory-1",
        "corpus_version": "hrl.benchmark.v1",
        "corpus_sha256": "a" * 64,
        "status": "blocked",
        "records": [
            {
                "model": "qwen3.5:4b",
                "role": "fast",
                "task_id": "classify-20-v1",
                "status": "blocked",
                "guard_state": "PAUSED",
                "reason_codes": ["protected_market_window"],
            }
        ],
        "excluded_candidates": [],
    }


def selections() -> dict[str, object]:
    return {
        "schema_version": "hrl.model_selections.v1",
        "inventory_id": "inventory-1",
        "tiers": {
            "no_llm": {"status": "available", "model": None},
            "fast": {"status": "unavailable", "model": None},
        },
    }


class ModelBenchmarkPublishTest(unittest.TestCase):
    def test_publication_binds_selections_and_three_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = publish_model_benchmark(document(), selections(), root)
            benchmark_payload = json.loads(paths["benchmark_json"].read_text())
            selection_payload = json.loads(paths["selections_json"].read_text())
            self.assertEqual(selection_payload["benchmark_id"], benchmark_payload["benchmark_id"])
            self.assertEqual(
                selection_payload["benchmark_sha256"],
                hashlib.sha256(paths["benchmark_json"].read_bytes()).hexdigest(),
            )
            lines = paths["checksums"].read_text().splitlines()
            self.assertEqual(len(lines), 3)
            for line in lines:
                digest, name = line.split("  ", 1)
                self.assertEqual(digest, hashlib.sha256((root / name).read_bytes()).hexdigest())

    def test_secret_rejection_preserves_previous_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = root / "model_benchmark.json"
            canonical.write_text("previous\n", encoding="utf-8")
            unsafe = document()
            unsafe["api_key"] = "do-not-publish"
            with self.assertRaises(PublicationSafetyError):
                publish_model_benchmark(unsafe, selections(), root)
            self.assertEqual(canonical.read_text(encoding="utf-8"), "previous\n")


if __name__ == "__main__":
    unittest.main()
