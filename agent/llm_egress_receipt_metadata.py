"""Content-free receipt envelope metadata, independent of request authority.

Schema identities describe the receipt and typed-renderer contracts, not an exact
executable version or Git revision. Old receipts need not carry these fields.
"""
from dataclasses import asdict
from datetime import UTC, datetime

RECEIPT_SCHEMA = 'llm-egress-receipt-v1'
RENDERER_SCHEMA = 'typed-outbound-request-v1'


def receipt_envelope(decision):
    """Record UTC construction time, not append order or an earlier event's time.

    Package-owned schema identities cannot be supplied by the request, provider,
    environment or configuration. Metadata joins the existing private hash chain;
    it is never added to provider wire bytes or used to grant authority.
    """
    return {**asdict(decision), 'recorded_at': datetime.now(UTC).isoformat(),
            'receipt_schema': RECEIPT_SCHEMA, 'renderer_schema': RENDERER_SCHEMA}
