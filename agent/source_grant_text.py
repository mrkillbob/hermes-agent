"""Read proof presentation bytes without conferring final egress authority."""
from pathlib import Path
from agent.llm_egress_firewall import SourceGrant


def _read_grant_text(grant: SourceGrant) -> str | None:
    from agent.source_context_preview import ContextPreviewGrant, verify_preview
    try:
        if isinstance(grant, ContextPreviewGrant):
            return verify_preview(grant)[0].decode('utf-8')
        lines = Path(grant.canonical_path).read_bytes().splitlines(keepends=True)
        return b"".join(lines[grant.line_start - 1 : grant.line_end]).decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return None
