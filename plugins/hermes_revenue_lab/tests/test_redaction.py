import unittest

from hermes_revenue_lab.inventory.redaction import (
    PublicationSafetyError,
    assert_publication_safe,
    sanitize_diagnostic,
)


class RedactionTest(unittest.TestCase):
    def test_rejects_secret_value(self) -> None:
        """Catches credential-bearing keys reaching public artifacts."""
        with self.assertRaises(PublicationSafetyError):
            assert_publication_safe({"api_key": "secret-value"})

    def test_rejects_hardware_uuid(self) -> None:
        """Catches a prohibited machine identifier embedded in text."""
        with self.assertRaises(PublicationSafetyError):
            assert_publication_safe({"note": "Hardware UUID: ABCD"})

    def test_accepts_allowlisted_inventory(self) -> None:
        """Catches an overbroad redaction gate rejecting safe metadata."""
        assert_publication_safe({"ollama": {"version": "0.32.14"}})

    def test_sanitizes_home_path_without_exposing_username(self) -> None:
        """Catches diagnostics persisting the local account name."""
        sanitized = sanitize_diagnostic("/Users/mikedemott/.local/bin/hermes")

        self.assertEqual("$HOME/.local/bin/hermes", sanitized)


if __name__ == "__main__":
    unittest.main()
