"""Caller-scoped receipt summaries; durable history remains in the shared ledger."""
import json
from .ledger import FeedbackLedger


def status() -> int:
    ledger = FeedbackLedger.for_current_profile()
    try:
        from .caller_repository import current_repository
        binding = current_repository()
        counts = ledger.status_counts(repository=binding.repository) if binding else ledger.status_counts()
    finally:
        ledger.close()
    print(json.dumps(counts, sort_keys=True))
    return 0


def merge_status(*, details: bool = False) -> int:
    ledger = FeedbackLedger.for_current_profile()
    try:
        from .caller_repository import current_repository
        binding = current_repository()
        repository = binding.repository if binding else None
        payload = {"merge": ledger.merge_status_counts(repository)}
        if details:
            from .merge_receipt_history import recent_merge_receipts
            payload["recent_receipts"] = recent_merge_receipts(ledger, repository=repository)
    finally:
        ledger.close()
    print(json.dumps(payload, sort_keys=True))
    return 0
