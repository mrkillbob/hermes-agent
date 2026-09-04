import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from hermes_revenue_lab.inventory.publish import publish_inventory, update_desktop_verdict
from hermes_revenue_lab.inventory.redaction import PublicationSafetyError


def safe_inventory(inventory_id: str = "inventory-1") -> dict[str, object]:
    return {
        "schema_version": "hrl.environment_inventory.v1",
        "inventory_id": inventory_id,
        "collected_at": "2026-08-20T12:00:00+00:00",
        "classification": "observed_busy",
        "unknowns": ["idle resource baseline unavailable"],
        "warnings": ["ollama_model_loaded"],
        "source_commands": [{"name": "ollama_list", "status": "available"}],
        "required_sections_blocked": [],
        "ollama": {"loaded_models": 1},
    }


class PublishTest(unittest.TestCase):
    def test_json_markdown_and_manifest_share_inventory_identity(self) -> None:
        """Catches independently rendered artifacts describing different runs."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = publish_inventory(safe_inventory(), root)

            document = json.loads(paths["environment_inventory_json"].read_text())
            markdown = paths["environment_inventory_md"].read_text()
            manifest = json.loads(paths["command_manifest_json"].read_text())
            self.assertEqual("inventory-1", document["inventory_id"])
            self.assertIn("`inventory-1`", markdown)
            self.assertEqual("inventory-1", manifest["inventory_id"])

    def test_secret_rejection_preserves_previous_canonical_file(self) -> None:
        """Catches rejected data replacing the last valid inventory."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = root / "environment_inventory.json"
            canonical.write_text("previous\n")
            unsafe = safe_inventory("inventory-unsafe")
            unsafe["api_key"] = "must-not-publish"

            with self.assertRaises(PublicationSafetyError):
                publish_inventory(unsafe, root)

            self.assertEqual("previous\n", canonical.read_text())
            self.assertTrue((root / "runs" / "inventory-unsafe" / "rejection.json").exists())

    def test_unknown_value_remains_json_null_and_markdown_unavailable(self) -> None:
        """Catches unknown numeric evidence becoming zero in either projection."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inventory = safe_inventory()
            inventory["swap_bytes"] = None
            paths = publish_inventory(inventory, root)

            document = json.loads(paths["environment_inventory_json"].read_text())
            self.assertIsNone(document["swap_bytes"])
            self.assertIn("unavailable", paths["environment_inventory_md"].read_text())

    def test_checksum_manifest_covers_four_payloads(self) -> None:
        """Catches a canonical payload changing outside checksum evidence."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = publish_inventory(safe_inventory(), root)
            lines = paths["inventory_checksums"].read_text().splitlines()

            self.assertEqual(4, len(lines))
            for line in lines:
                digest, name = line.split("  ", 1)
                payload = root / name
                self.assertEqual(digest, hashlib.sha256(payload.read_bytes()).hexdigest())

    def test_desktop_update_preserves_inventory_identity_and_refreshes_checksums(self) -> None:
        """Catches Desktop smoke evidence detaching from its inventory or staling checksums."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            publish_inventory(safe_inventory(), root)
            update_desktop_verdict(
                root,
                {
                    "status": "available",
                    "verified_at": "2026-08-20T12:01:00+00:00",
                    "gateway_name": "Hermes Revenue Lab",
                    "endpoint": "http://127.0.0.1:9120",
                    "http_status": 200,
                    "desktop_app_test_status": "reachable",
                },
            )

            update_desktop_verdict(
                root,
                {
                    "status": "available",
                    "verified_at": "2026-08-20T12:02:00+00:00",
                    "gateway_name": "Hermes Revenue Lab",
                    "endpoint": "http://127.0.0.1:9120",
                    "http_status": 200,
                    "token_auth_verified": True,
                },
            )

            verdict = json.loads((root / "desktop_connection_verdict.json").read_text())
            self.assertEqual(verdict["inventory_id"], "inventory-1")
            self.assertEqual(verdict["desktop_app_test_status"], "reachable")
            self.assertTrue(verdict["token_auth_verified"])
            run_verdict = root / "runs" / "inventory-1" / "desktop_connection_verdict.json"
            self.assertEqual(json.loads(run_verdict.read_text()), verdict)
            for line in (root / "inventory_checksums.sha256").read_text().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(digest, hashlib.sha256((root / name).read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
