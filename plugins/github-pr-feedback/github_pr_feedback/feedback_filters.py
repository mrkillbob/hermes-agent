"""Shared pure feedback exclusions; no controller, transport, or state access."""

from __future__ import annotations

import re

from .github_client import MAX_FEEDBACK_BODY_CHARS, Feedback
from .policy import PluginPolicy, is_codex_review_request

_SELF_RESOLUTION_PREFIXES = (
    "addressed ",
    "implemented in ",
    "resolved ",
    "fixed at ",
    "fixed in ",
    "fixed both ",
    "fixed the ",
    "conflict refresh completed at exact head ",
    "base refresh:",
    "base refresh ",
    "base refresh for this pr:",
    "rebased ",
    "split started with ",
)

_ACTION_REMAINS_MARKERS = (
    "blocker remains",
    "still needs work",
    "not fixed",
    "follow-up required",
    "todo",
    "unresolved",
)

_BOUNDED_ACTION_REMAINS = re.compile(
    r"\b(?:still failing|still fails|needs fixing|needs repair|remaining failures?)\b"
)

_BARE_FAILS = re.compile(r"\bfails\b")

_FAILURE_LANES = (
    "run_static_lane.py",
    "run_hygiene_lane.py",
    "static lane",
    "static-lane",
    "hygiene lane",
    "hygiene-lane",
)

_HISTORIC_FAILURE_CONTEXT = (
    "reproduced",
    "reproduction",
    "reported",
    "before the fix",
    "prior to the fix",
    "pristine receipt",
)

_RESOLVED_AFTER_FAILURE = (
    "with the fix",
    "after the fix",
    "after repair",
    "root cause and fix",
)

_LANE_PASS_EVIDENCE = re.compile(
    r"\b(?:status\s*[:=]?\s*pass|rc\s*=\s*0|passes|passed)|->\s*pass\b"
)

_CODEX_REVIEW_ENVELOPE_PREFIX = (
    "### 💡 codex review here are some automated review suggestions for this pull request."
)

_CI_RECEIPT_MARKER = re.compile(
    r"<!--\s*pr-ci-receipt:v1\b[^>]*\bhead=([0-9a-f]{40})\s*-->",
    flags=re.IGNORECASE,
)


def _is_self_resolution_receipt(feedback: Feedback, *, owner_login: str) -> bool:
    """Suppress only high-confidence author receipts that describe completed work."""

    if feedback.kind not in {"issue_comment", "review_comment"}:
        return False
    if feedback.reviewer.login.casefold() != owner_login.casefold():
        return False
    if len(feedback.body) >= MAX_FEEDBACK_BODY_CHARS:
        return False
    body = " ".join(feedback.body.casefold().split())
    if not body:
        return False
    exact_fixed_commit = re.match(r"fixed in [0-9a-f]{40,64}\b", body) is not None
    if (
        exact_fixed_commit
        and any(marker in body for marker in ("verification:", "focused gate:"))
        and (_LANE_PASS_EVIDENCE.search(body) is not None or "now succeeds" in body)
        and not any(marker in body for marker in _ACTION_REMAINS_MARKERS)
        and _BOUNDED_ACTION_REMAINS.search(body) is None
    ):
        return True
    semantic_static_repair = (
        (
            "static-lane repair" in body
            or ("static lane" in body and "fix" in body)
            or ("static-lane failure" in body and "fix" in body)
        )
        and re.search(r"\b(?:commit|head)\s*:?\s*`?[0-9a-f]{40,64}`?\b", body) is not None
        and any(marker in body for marker in ("verification", "evidence", "after the fix"))
        and _LANE_PASS_EVIDENCE.search(body) is not None
        and any(
            marker in body
            for marker in (
                "merge remains gated",
                "no safety gate was relaxed",
                "no required check",
            )
        )
        and not any(marker in body for marker in _ACTION_REMAINS_MARKERS)
        and _BOUNDED_ACTION_REMAINS.search(body) is None
    )
    if semantic_static_repair:
        return True
    if _has_unresolved_action(body):
        return False
    semantic_owner_completion = (
        body.startswith(
            (
                "thanks for the careful review",
                "pushed ",
                "updated ",
                "integrated ",
                "exact-head verification of merge head ",
            )
        )
        and any(
            marker in body
            for marker in (
                "addressed",
                "addressing",
                "checked against the tree",
                "complete exact-head repair",
                "integrated the latest approved",
                "confirms the re-review findings",
            )
        )
        and (
            _LANE_PASS_EVIDENCE.search(body) is not None
            or re.search(r"\btests?\s+pass\b", body) is not None
            or "neither required a code change" in body
            or "no code changes were made" in body
        )
        and (
            re.search(r"\b[0-9a-f]{40,64}\b", body) is not None
            or body.startswith(("updated ", "integrated "))
        )
    )
    if semantic_owner_completion:
        return True
    reconciled_ci_receipt = (
        body.startswith("re: local pr ci audit receipt ")
        and "already resolved" in body
        and re.search(r"\b[0-9a-f]{40,64}\b", body) is not None
        and any(marker in body for marker in ("run_static_lane.py", "run_hygiene_lane.py"))
        and (
            _LANE_PASS_EVIDENCE.search(body) is not None
            or re.search(r"\bchecks?\s+pass\b", body) is not None
        )
        and any(
            marker in body for marker in ("no edit to this branch", "no source files were changed")
        )
        and any(marker in body for marker in ("no merge action", "no merge performed"))
    )
    if reconciled_ci_receipt:
        return True
    if (
        "pr-maintenance-receipt:v1" in body
        and "status=completed" in body
        and re.search(r"\bhead=[0-9a-f]{40,64}\b", body) is not None
    ):
        return True
    semantic_base_refresh = (
        re.search(r"\bbase\b", body) is not None
        and re.search(r"\brefresh(?:ed)?\b", body) is not None
        and re.search(r"\b(?:merge|merged|pushed|fast-forward)\b", body) is not None
        and re.search(r"\b[0-9a-f]{40,64}\b", body) is not None
        and _LANE_PASS_EVIDENCE.search(body) is not None
    )
    if semantic_base_refresh:
        return True
    if body.startswith(_SELF_RESOLUTION_PREFIXES):
        return True
    if body.startswith("resolved ") and "verification:" in body and "no merge performed" in body:
        return True
    if (
        body.startswith("## static lane fix")
        and "**commit:**" in body
        and "**focused verification" in body
        and "**files changed:**" in body
    ):
        return True
    completed_ci_repair = (
        body.startswith("local-ci static-lane repair for this pr is in place at commit ")
        or body.startswith("repaired the local-ci static-lane failure reported for exact head ")
        or (body.startswith("local ci repair for receipt ") and " landed at head " in body)
        or body.startswith("static-lane repair pushed at ")
    )
    exact_head_evidence = (
        re.search(r"\b(?:commit|head)\s+`?[0-9a-f]{40,64}`?\b", body) is not None
        or re.match(r"static-lane repair pushed at `?[0-9a-f]{40,64}`?\b", body) is not None
    )
    verification_evidence = any(
        marker in body for marker in ("re-validated", "verification:", "verification,", "evidence:")
    )
    lane_evidence = any(
        marker in body
        for marker in (
            "run_static_lane.py",
            "run_hygiene_lane.py",
            "static lane",
            "static-lane",
            "hygiene lane",
            "hygiene-lane",
        )
    )
    passing_evidence = (
        any(
            marker in body
            for marker in (
                "status: pass",
                "status=pass",
                "checks passed",
                "zero findings",
                "rc=0",
            )
        )
        or _LANE_PASS_EVIDENCE.search(body) is not None
    )
    merge_remains_gated = any(
        marker in body
        for marker in (
            "no merge was performed",
            "no merge performed",
            "merge remains gated",
            "merge remains controlled",
            "no ci configuration, required checks, or safety gates were modified",
        )
    ) or ("no gate" in body and "relaxed" in body)
    if (
        completed_ci_repair
        and exact_head_evidence
        and verification_evidence
        and lane_evidence
        and passing_evidence
        and merge_remains_gated
    ):
        return True
    if any(
        marker in body
        for marker in (
            "no additional change required",
            "no additional changes required",
            "no additional commit is required",
            "no additional commits are required",
            "no further code change required",
            "no further code changes required",
            "no further source change required",
            "no further source changes required",
            "no further change needed",
            "no further changes needed",
            "no further audit rerun performed",
        )
    ):
        return True
    verification_only = body.startswith(("verification at head ", "validated against exact head "))
    if (
        verification_only
        and exact_head_evidence
        and "focused verification" in body
        and _LANE_PASS_EVIDENCE.search(body) is not None
        and any(
            marker in body
            for marker in (
                "no history rewrite",
                "no source files were changed",
                "no ci configuration, required checks, or safety gates were modified",
                "failure no longer reproduces",
            )
        )
    ):
        return True
    if " are repaired in " in body and "verification" in body:
        return True
    audit_marker = any(
        marker in body
        for marker in (
            "local ci audit",
            "local pr ci audit",
            "re-audit reconciliation",
            "hygiene-lane receipt follow-up",
            "independent validation of this audit",
        )
    )
    inherited_marker = "pre-existing" in body and any(
        marker in body
        for marker in (
            "stable base",
            "stable tip",
            "base tip",
            "canonical base",
            "branch lineage",
            "shared-base",
        )
    )
    routed_separately = any(
        marker in body
        for marker in (
            "separate repair",
            "not introduced by this pr",
            "not regressions of this pr",
        )
    )
    if audit_marker and inherited_marker and routed_separately:
        return True
    # A self-authored supersession claim is not canonical repository evidence:
    # the named replacement may later close unmerged, leaving the defect open.
    return False


def _has_unresolved_action(body: str) -> bool:
    if any(marker in body for marker in _ACTION_REMAINS_MARKERS):
        return True
    if _BOUNDED_ACTION_REMAINS.search(body) is not None:
        return True
    for match in _BARE_FAILS.finditer(body):
        clause_start = max(
            body.rfind(separator, 0, match.start()) for separator in (". ", "! ", "? ")
        )
        context = body[clause_start + 1 : match.end()]
        after = body[match.end() :]
        factual_history = any(
            marker in context for marker in _HISTORIC_FAILURE_CONTEXT
        ) and _same_lane_passes_after_resolution(context, after)
        if not factual_history:
            return True
    return False


def _same_lane_passes_after_resolution(context: str, after: str) -> bool:
    transitions = [after.find(marker) for marker in _RESOLVED_AFTER_FAILURE if marker in after]
    if not transitions:
        return False
    post_fix = after[min(transitions) :]
    for lane in _FAILURE_LANES:
        if lane not in context:
            continue
        lane_start = post_fix.find(lane)
        if lane_start < 0:
            continue
        boundaries = [
            post_fix.find(separator, lane_start + len(lane))
            for separator in (". ", "; ", "! ", "? ")
        ]
        lane_end = min(
            (boundary for boundary in boundaries if boundary >= 0), default=len(post_fix)
        )
        if _LANE_PASS_EVIDENCE.search(post_fix[lane_start:lane_end]) is not None:
            return True
    return False


def _is_non_actionable_review_container(feedback: Feedback) -> bool:
    """Suppress review-level envelopes whose actionable findings arrive separately."""

    if feedback.kind != "review":
        return False
    body = " ".join(feedback.body.casefold().split())
    if not body:
        return True
    return (
        feedback.is_bot
        and body.startswith(_CODEX_REVIEW_ENVELOPE_PREFIX)
        and "p1 badge" not in body
        and "p2 badge" not in body
        and not any(marker in body for marker in _ACTION_REMAINS_MARKERS)
    )


def _is_advisory_lgtm_report(feedback: Feedback) -> bool:
    """Skip reference-only AI review summaries that request no PR changes."""

    if feedback.kind not in {"issue_comment", "review_comment", "review"}:
        return False
    body = " ".join(feedback.body.casefold().split())
    reference_report = "automated review for reference; please use your judgment" in body
    legacy_verdict = (
        re.search(r"\bverdict\s*:\s*lgtm\b", body) is not None and "non-blocking:" in body
    )
    no_findings_report = (
        feedback.kind == "issue_comment"
        and re.search(r"\bno (?:blocking or non-blocking issues found|findings(?:\s|[—:,.]))", body)
        is not None
    )
    return (
        reference_report
        and (legacy_verdict or no_findings_report)
        and not any(
            marker in body
            for marker in (
                "changes requested",
                "action required",
                "blocking finding",
                "must be fixed",
                "needs to be fixed",
                "[p1]",
                "[p2]",
                "p1 badge",
                "p2 badge",
                "however",
                "except",
                "please fix",
                "recommend",
                "blocker remains",
            )
        )
    )


_CODEX_REVIEW_SUMMARY_MARKER = "codex-pull-request-review-summary"


def _is_codex_review_summary_tracker(feedback: Feedback) -> bool:
    """Codex's own running review-status tracker is never itself a finding.

    Posted as a plain issue comment (not a GitHub "review", so
    _is_non_actionable_review_container above never sees it), it only ever
    reports which review ran, when, and against which commit -- a markdown
    table, nothing else. An actual Codex suggestion or finding, if any,
    arrives as a separate ordinary review/issue comment without this marker
    and is admitted normally. Dispatching this tracker as if it were
    actionable feedback wastes a repair attempt on a comment that never
    asked for anything, and its unicode-heavy table content has been
    observed tripping a fallback provider's egress secret-scanner as a
    false positive.
    """

    return feedback.is_bot and _CODEX_REVIEW_SUMMARY_MARKER in feedback.body


def non_actionable_feedback_reason(
    policy: PluginPolicy, feedback: Feedback, *, owner_login: str
) -> str | None:
    """Classify advisory feedback consistently at intake and dispatch retirement."""
    if _is_non_actionable_review_container(feedback):
        return "non_actionable_review_container"
    if _is_advisory_lgtm_report(feedback):
        return "advisory_lgtm_report"
    if _is_codex_review_summary_tracker(feedback):
        return "codex_review_summary_tracker"
    if (
        feedback.kind == "issue_comment"
        and feedback.reviewer.login.casefold() == owner_login.casefold()
        and re.match(
            r"^@[A-Za-z0-9_-]+\s+Please review the current upstream PR head\b", feedback.body
        )
        and "specifically for your AI review" in feedback.body
        and not re.search(
            r"\[P[0-3]\]|\b(?:fix|repair|regression|bug)\b", feedback.body, re.IGNORECASE
        )
    ):
        return "self_review_request"
    if is_codex_review_request(feedback.body):
        return "codex_review_request"
    if (
        feedback.reviewer.login.casefold() == owner_login.casefold()
        and _CI_RECEIPT_MARKER.search(feedback.body) is not None
    ):
        # The deterministic local-CI publisher owns this marker.  Suppress
        # it independently of a profile-local ledger: workers may record
        # the receipt under their profile while the global scanner reads
        # the comment from the shared GitHub account.
        return "self_ci_receipt"
    if (
        feedback.kind == "issue_comment"
        and feedback.reviewer.login.casefold() == owner_login.casefold()
        and (
            re.match(
                r"^(?:#{1,6}\s+)?(?:Refactor checkpoint:\s*|Implementation update\s*[—-]\s*)"
                r"`[0-9a-f]{7,40}`(?:[^\n]*)\n",
                feedback.body.strip(),
                flags=re.IGNORECASE,
            )
            or (
                len(feedback.body) < MAX_FEEDBACK_BODY_CHARS
                and re.match(
                    r"^(?:#{1,6}\s+)?Structural correction and handoff\s*\n",
                    feedback.body.strip(),
                    flags=re.IGNORECASE,
                )
                and re.search(r"`[^`\n]+\.py` is [0-9,]+ lines\b", feedback.body)
                and re.search(r"`docs/superpowers/handoffs/[A-Za-z0-9_./-]+\.md`", feedback.body)
                and "pytest remains deferred until production refactoring is complete"
                in feedback.body.casefold()
                and "pr remains draft" in feedback.body.casefold()
            )
        )
        and not re.search(
            r"\[P[0-3]\]|\b(?:please|must|need(?:s)? to|request(?:ed)?|regression|bug)\b",
            feedback.body,
            flags=re.IGNORECASE,
        )
    ):
        return "self_progress_checkpoint"
    if _is_self_resolution_receipt(feedback, owner_login=owner_login):
        return "self_resolution_receipt"
    identity = policy.github_identity
    if (
        identity is not None
        and feedback.reviewer.login.casefold() == identity.expected_login.casefold()
        and feedback.kind in {"issue_comment", "review_comment"}
        and len(feedback.body) < MAX_FEEDBACK_BODY_CHARS
        and re.search(
            r"<!--\s*pr-maintenance-receipt:v1\s+status=completed\s+"
            r"kind=\w+\s+head=[0-9a-f]{40,64}\s*-->",
            feedback.body,
        )
        is not None
    ):
        # Governed automation has a separate identity from the PR owner.
        # Its completion receipts must not become new repair requests.
        return "self_resolution_receipt"
    return None
