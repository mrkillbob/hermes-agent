"""Passive intake diagnostics without clients, scanning or persistence."""
from datetime import UTC, datetime


def diagnose_intake(ctx, repository, *, now=None):
    """Inspect current policy/context only; never predict scan success or historical cause.

    Caller must retain the existing bind_cli/ScopedContext frontdoor. This entry
    must not be called by scan-intake or any scheduled poller automatically.
    """
    from .intake_cli import load_intake_policy
    from .intake_context import registered_control_root, require_context
    from .intake_only import SCOPES

    result = {'status': 'not_evaluated', 'created': 0, 'historical_cause': 'unknown',
              'checks': [], 'not_checked': ['credentials', 'github', 'receipts', 'card_write']}
    if repository not in SCOPES:
        result.update(status='rejected', reason='repository_scope_unavailable')
        return result
    phase = 'control_root'
    try:
        root = registered_control_root(ctx, SCOPES[repository])
        result['checks'].append({'phase': phase, 'status': 'passed'})
        phase = 'policy'
        policy = load_intake_policy(ctx, repository)
        result['checks'].append({'phase': phase, 'status': 'passed'})
        phase = 'time_policy'
        policy.check(datetime.now(UTC) if now is None else now)
        result['checks'].append({'phase': phase, 'status': 'passed'})
        phase = 'passive_context'
        require_context(ctx, root, SCOPES[repository])
        result['checks'].append({'phase': phase, 'status': 'passed'})
    except Exception:  # health: allow BLE001 -- Diagnostic boundary returns fixed phase codes, never host configuration or exception details.
        # Exception type/text can contain credentials, paths or raw host config.
        result['checks'].append({'phase': phase, 'status': 'rejected'})
        result.update(status='rejected', reason=phase + '_unavailable')
        return result
    result['status'] = 'passive_checks_passed'
    return result
