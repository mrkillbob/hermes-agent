"""Reject sensitive data before any HRL-0 artifact is published."""

import re
from typing import Any


class PublicationSafetyError(ValueError):
    """Raised when a candidate artifact contains prohibited data."""


_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "hardware_uuid",
        "password",
        "provisioning_udid",
        "secret",
        "serial_number",
        "token",
    }
)
_SENSITIVE_LABEL = re.compile(
    r"(?i)(api[_ -]?key|authorization|cookie|hardware uuid|password|"
    r"provisioning udid|serial number|token)\s*[:=]"
)
_HOME_PATH = re.compile(r"/Users/mikedemott(?=/|\b)")


def _normalized_key(key: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(key).strip().lower())


def assert_publication_safe(value: object) -> None:
    """Recursively reject credential keys and labeled sensitive values."""

    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _normalized_key(key)
            if normalized in _SENSITIVE_KEYS:
                raise PublicationSafetyError(f"sensitive key: {normalized}")
            assert_publication_safe(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            assert_publication_safe(child)
        return
    if isinstance(value, str) and _SENSITIVE_LABEL.search(value):
        raise PublicationSafetyError("sensitive labeled value")


def sanitize_diagnostic(text: str) -> str:
    """Remove the local account path from bounded non-secret diagnostics."""

    return _HOME_PATH.sub("$HOME", text)
