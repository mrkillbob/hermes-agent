from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from hermes_revenue_lab.inventory.redaction import PublicationSafetyError
from hermes_revenue_lab.routing.ledger import append_routing_event
from hermes_revenue_lab.routing.types import RoutingEvent


def event(**overrides: object) -> RoutingEvent:
    values = {
        "event_id": "event-001",
        "task_id": "classify.batch-1",
        "requested_tier": "fast",
        "actual_tier": "fast",
        "actual_model": "qwen3.5:4b",
        "model_digest": "2a654d98e6fb",
        "escalation_reason": None,
        "started_at": "2026-08-21T00:00:00Z",
        "ended_at": "2026-08-21T00:00:01Z",
        "wall_time_seconds": 1.0,
        "task_result": "succeeded",
        "retries": 0,
        "estimated_compute_cost": {
            "basis": "measured_local_wall_time",
            "local_compute_seconds": 1.0,
            "monetary_cost": None,
            "electricity_cost": None,
        },
        "success": True,
    }
    values.update(overrides)
    return RoutingEvent(**values)


class RouterLedgerTest(unittest.TestCase):
    def test_append_writes_canonical_private_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "lab"
            root.mkdir()
            path = root / ".hermes" / "router" / "events.jsonl"

            append_routing_event(path, event(), allowed_root=root)
            append_routing_event(path, event(event_id="event-002"), allowed_root=root)

            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(["event-001", "event-002"], [row["event_id"] for row in rows])
            self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
            self.assertNotIn(" ", path.read_text().splitlines()[0])

    def test_path_escape_is_rejected_before_directory_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "lab"
            root.mkdir()
            outside = base / "outside" / "events.jsonl"

            with self.assertRaisesRegex(ValueError, "outside Revenue Lab"):
                append_routing_event(outside, event(), allowed_root=root)

            self.assertFalse(outside.parent.exists())

    def test_symlink_target_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "lab"
            root.mkdir()
            real = root / "real.jsonl"
            real.write_text("", encoding="utf-8")
            link = root / "events.jsonl"
            link.symlink_to(real)

            with self.assertRaisesRegex(ValueError, "symlink"):
                append_routing_event(link, event(), allowed_root=root)

    def test_secret_labeled_event_is_rejected_without_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "lab"
            root.mkdir()
            path = root / "events.jsonl"

            with self.assertRaises(PublicationSafetyError):
                append_routing_event(
                    path,
                    event(escalation_reason="token: must-not-persist"),
                    allowed_root=root,
                )

            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
