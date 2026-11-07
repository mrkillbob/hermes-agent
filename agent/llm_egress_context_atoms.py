"""Closed public protocol vocabulary shared by literal and wrapped scans."""

from collections.abc import Callable

PUBLIC_CONTEXT_ATOMS = frozenset(
    {
        "PAPER_SAFETY_SENTINEL_OK",
        "HERMES_BACKEND_READY",
        "FAILURE",
        "YAML",
    }
)


# Fixed literal words the scans must not treat as payloads. Only these exact
# spellings are exempt; arbitrary uppercase words and hyphenated blobs remain
# scanned.
SAFE_CONTEXT_WORDS = frozenset({
    "PASS",
    "WARN",
    "SUMMARY",
    "REQUIREMENTS",
    "AVAILABILITY",
    "HANDLING",
    "VERIFICATION",
    "ADVISORY",
    "FAIL",
    "SHA1",
    "CRITICAL",
    # Fixed command-syntax metavariables in the built-in Kanban guidance.
    # They are not payloads even though their short all-caps forms can decode
    # as canonical Base64 by coincidence.
    "APPROVE",
    "TEXT",
    "TOOL", "WINS", "IDLE",
    # The literal ``[REDACTED]`` placeholder that redaction templates write into
    # prompt text (e.g. the compressor's summary prompt). It is 8 all-caps
    # letters from the Base64 alphabet, so it round-trips canonically by pure
    # arithmetic coincidence; it is the absence of content, never a payload.
    "REDACTED",
    "Cross-department",  # Ordinary Kanban prose, not arbitrary hyphenated blobs.
}) | PUBLIC_CONTEXT_ATOMS


def canonical_chunked_context_atom(
    joined: str, canonical: Callable[[str], bool]
) -> bool:
    """A literal-word exemption never authorizes whitespace-wrapped encoding."""
    return joined in PUBLIC_CONTEXT_ATOMS or canonical(joined)
