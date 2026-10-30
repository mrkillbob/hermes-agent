"""Exact-receipt retries under the existing caller-repository reservation."""
import json


def retry(ctx, args) -> int:
    from .cli import (
        _load_policy_from_context, FeedbackReceipt, _exclusive_scan_lock,
        FeedbackLedger, RepairController, _github_client, KanbanSubprocessClient,
        get_default_hermes_root, _controller, _scan_payload,
    )
    try:
        policy = _load_policy_from_context(ctx)
        receipt = FeedbackReceipt(
            args.repository,
            args.pr_number,
            args.feedback_kind,
            args.feedback_id,
            args.head_sha,
        )
    except ValueError:
        print(json.dumps({"status": "invalid_configuration"}, sort_keys=True))
        return 1
    with _exclusive_scan_lock() as acquired:
        if not acquired:
            print(json.dumps({"status": "scan_already_running"}, sort_keys=True))
            return 1
        ledger = FeedbackLedger.for_current_profile()
        try:
            if receipt.feedback_kind == "pr_repair":
                result = RepairController(
                    policy, ledger, _github_client(policy), KanbanSubprocessClient(),
                    control_home=get_default_hermes_root(),
                ).scan(retry_receipt=receipt)
            else:
                result = _controller(policy, ledger).retry_failed(receipt)
        finally:
            ledger.close()
    print(json.dumps(_scan_payload(result), sort_keys=True))
    return 1 if result.degraded else 0
