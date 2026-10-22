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


def canonical_chunked_context_atom(
    joined: str, canonical: Callable[[str], bool]
) -> bool:
    """A literal-word exemption never authorizes whitespace-wrapped encoding."""
    return joined in PUBLIC_CONTEXT_ATOMS or canonical(joined)
