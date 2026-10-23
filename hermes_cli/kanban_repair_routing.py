"""Deterministic handoff from Task Orchestrator to repair profiles."""

from __future__ import annotations

import re
from typing import Optional


_CRON_PURPOSE_TERMS = ("scheduled job", "cron job")
# Title alternatives are evaluated at their owner's existing table position,
# preserving priority for PR audits and concrete maintenance/quota failures.
_TITLE_REPAIR_RULES = {
    "market-data-authority-auditor": r"^market[ -]data (?:freshness|authority)\b",
    "hermes-maintenance-steward": r"^repair broken \.venv runtime link in hermes-agent worktree\s+",
}

# These titles name assessment/diagnostic work directly. Body inventories can
# quote unrelated jobs or repository paths, so they cannot establish this intent.
_TITLE_INTAKE_RULES = (
    (r"^(?:vite lock stale|regenerate python lock|local tooling refresh|github actions pin drift)"
     r"(?:\s*[:—–]|\s+-\s+|\s*$)", "dependency-tooling-health-sentinel"),
    (r"^diagnostic experiment:\s+.*\blunabot replay\b", "research-experiment-runner"),
)

# These direct operational blockers apply only inside matched intake-title
# families, where they precede title-only ownership.
_OPERATIONAL_BLOCKER_RULES = (
    (r"\b(?:the )?automation credential is missing\b",
     "hermes-maintenance-steward"),
    (r"\b(?:the )?gh auth failure\b",
     "hermes-maintenance-steward"),
    (r"\b(?:the )?provider returned usage limits\b",
     "hermes-maintenance-steward"),
    (r"\b(?:the )?(?:(?:workflow|provider|account|automation) )?(?:"
     r"quota (?:is|was|has been) (?:exhausted|exceeded)|"
     r"quota failures? (?:are|were|is|was)? ?(?:blocking|blocked|preventing|prevented|failing)|"
     r"usage limits (?:are|were|have been) (?:exceeded|exhausted)|"
     r"(?:quota|usage limits) (?:blocked|prevented|failed))\b",
     "hermes-maintenance-steward"),
    (r"\b(?:the )?lunabot diagnostics? (?:"
     r"failed|(?:has|have) failed|(?:is|are|was|were) (?:blocked|unavailable)|"
     r"(?:returned|reported|encountered) (?:an )?error)\b",
     "hermes-maintenance-steward"),
)

_INVENTORY_LABEL = re.compile(
    r"(?:^|(?<=[.!?]))\s*(?:inventory|quoted(?:\s+inventory)?)\s*:", re.IGNORECASE,
)
_FENCE_OPEN = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_FENCE_CLOSE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*$")
_BLOCKQUOTE = re.compile(r"^\s{0,3}>")
_BLOCKQUOTE_EMPTY = re.compile(r"^\s{0,3}>\s*$")
_BLOCKER_NEGATION = re.compile(r"\b(?:no|if|whether|unless)\s+(?:the\s+)?(?:[`'\"‘“]\s*)*$")
_WHEN_INQUIRY = re.compile(r"\bwhen\s+(?:the\s+)?(?:[`'\"‘“]\s*)*$")
_INQUIRY_VERB = re.compile(r"\b(?:check|review|record|inspect|determine|verify)\s*$")
_INLINE_QUOTES = {"'": "'", '"': '"', "‘": "’", "“": "”"}
_INLINE_BARRIER = "\ue000"
_EXAMPLE_CUE = re.compile(
    r"(?:\bexample(?:\s+text)?\s*[:,-]?|\bfor example\s*,?|\bcompare the phrase)\s*$",
    re.IGNORECASE,
)
_EXAMPLE_SUFFIX = re.compile(r"^\s+is an example sentence\b", re.IGNORECASE)


def _fence_closes(line: str, marker: tuple[str, int]) -> bool:
    """Whether a Markdown fence line closes the active fence."""
    match = _FENCE_CLOSE.match(line)
    return bool(match and match.group(1)[0] == marker[0] and len(match.group(1)) >= marker[1])


def _inline_quote_end(value: str, start: int, closing: str) -> int | None:
    """Find a paired inline quote without crossing a line or contraction."""
    index = start + 1
    while index < len(value):
        char = value[index]
        if char in "\r\n":
            return None
        if char == "\\":
            index += 2
            continue
        if char == closing:
            if (closing in {"'", "’"} and index + 1 < len(value)
                    and value[index - 1].isalnum() and value[index + 1].isalnum()):
                index += 1
                continue
            return index
        index += 1
    return None


def _inline_code_end(value: str, start: int, run_length: int) -> int | None:
    """Find an equal-length inline backtick run on the same line."""
    index = start + run_length
    while index < len(value):
        if value[index] in "\r\n":
            return None
        if value[index] == "`":
            end = index
            while end < len(value) and value[end] == "`":
                end += 1
            if end - index == run_length:
                return index
            index = end
        else:
            index += 1
    return None


def _inline_span_is_example(value: str, start: int, end: int) -> bool:
    """Whether a paired inline span is explicitly introduced as an example."""
    prefix = value[:start].rsplit("\n", 1)[-1].rsplit("\r", 1)[-1]
    suffix = value[end + 1:].splitlines()[0] if value[end + 1:] else ""
    return bool(_EXAMPLE_CUE.search(prefix) or _EXAMPLE_SUFFIX.match(suffix))


def _mask_inline_examples(value: str) -> str:
    """Mask only explicitly example-cued inline spans, retaining line breaks."""
    masked: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char in "\r\n":
            masked.append(char)
            index += 1
            continue

        if char == "`":
            end_run = index + 1
            while end_run < len(value) and value[end_run] == "`":
                end_run += 1
            run_length = end_run - index
            if run_length < 3:
                close = _inline_code_end(value, index, run_length)
                if close is not None:
                    end = close + run_length - 1
                    if _inline_span_is_example(value, index, end):
                        masked.append(_INLINE_BARRIER)
                    else:
                        masked.append(value[index:end + 1])
                    index = close + run_length
                    continue
            masked.append(value[index:end_run])
            index = end_run
            continue

        closing = _INLINE_QUOTES.get(char)
        opens = closing is not None
        if char == "'" and index > 0 and (value[index - 1].isalnum() or value[index - 1] == "_"):
            opens = False
        if opens:
            close = _inline_quote_end(value, index, closing)
            if close is not None:
                if _inline_span_is_example(value, index, close):
                    masked.append(_INLINE_BARRIER)
                else:
                    masked.append(value[index:close + 1])
                index = close + 1
                continue
        masked.append(char)
        index += 1
    return "".join(masked)


def _retained_operational_segments(value: str) -> list[str]:
    """Remove labeled inventory, Markdown quotes, and fenced examples."""
    retained: list[str] = []
    inventory = False
    inventory_has_content = False
    quoted_paragraph = False
    fence: tuple[str, int] | None = None

    for line in value.splitlines():
        if fence:
            if inventory and line.strip():
                inventory_has_content = True
            if _fence_closes(line, fence):
                fence = None
            continue

        opening = _FENCE_OPEN.match(line)
        if opening:
            marker = opening.group(1)
            fence = (marker[0], len(marker))
            if inventory:
                inventory_has_content = True
            continue

        if inventory:
            if line.strip():
                inventory_has_content = True
            elif inventory_has_content:
                inventory = False
                inventory_has_content = False
            continue

        if _BLOCKQUOTE_EMPTY.match(line):
            quoted_paragraph = False
            continue
        if quoted_paragraph:
            if not line.strip():
                quoted_paragraph = False
            continue
        if _BLOCKQUOTE.match(line):
            quoted_paragraph = True
            continue

        label = _INVENTORY_LABEL.search(line)
        if label:
            prefix = line[:label.start()].strip()
            if prefix:
                retained.append(prefix)
            inventory = True
            inventory_has_content = bool(line[label.end():].strip())
            continue

        if line.strip():
            retained.append(line)
    return retained


def _has_operational_blocker(segments: list[str]) -> bool:
    """Find concrete blockers while skipping immediate negative conditions."""
    for segment in segments:
        normalized = segment.casefold()
        for pattern, _profile in _OPERATIONAL_BLOCKER_RULES:
            for match in re.finditer(pattern, normalized):
                prefix = normalized[:match.start()]
                if _BLOCKER_NEGATION.search(prefix):
                    continue
                when = _WHEN_INQUIRY.search(prefix)
                if when:
                    before_when = prefix[:when.start()].rstrip()
                    if not before_when or _INQUIRY_VERB.search(before_when):
                        continue
                return True
    return False


# Ordered from narrowest scope to broadest so a PR audit is not swallowed by
# the generic cron or maintenance rules.
_REPAIR_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    # Match the actual work type before incidental evidence quoted in a card's
    # instructions.  PR feedback cards often contain phrases such as
    # "not a hermes command" as fail-closed guidance; those phrases must not
    # divert them to maintenance instead of the fixed PR-feedback worker.
    (("local pr ci", "audit pr", "audit this pull request"), "pr-local-ci-auditor"),
    # Maintenance conditions take priority over generic PR-feedback routing: a card
    # with "GitHub PR feedback" in the title but "usage limits" in the body failed
    # because of quota exhaustion, not a PR-feedback logic error.
    (("gh auth failure", "automation credential is missing", "usage limits", "untrusted receipt", "missing hermes_cli", "audit-pr unavailable", "inspect-pr unavailable", "audit_deferred"), "hermes-maintenance-steward"),
    (("github pr feedback", "complete-feedback", "inspect-pr"), "pr-repair-steward"),
    (("worktree audit", "worktree governance", "worktree & change governance scan"), "worktree-change-governance-steward"),
    (("federated runner", "federation", "federated"), "federation-steward"),
    (("gui_command_runner.py", "start_gui_command", "orphaned subprocess",
      "gui launch/cancel", "stale-reconciliation race", "stale reconciliation",
      "_reconcile_stale_active_run", "cancel_gui_run"), "runtime-correctness-steward"),
    (("pytest suite", "test suite", "test coverage"), "test-contract-steward"),
    (("content discovery", "useful content", "community discovery"), "nerdy-content-scout"),
    (("synthesize gaps", "route bounded children", "synthesis"), "synthesizer"),
    (("market data", "authority freshness", "stale market"), "market-data-authority-auditor"),
    # White Knight intake owns upstream issue relevance and workspace admission.
    (("white knight", "issue relevance intake"), "hermes-white-knight"),
    (("upstream pr", "upstream issue", "nousresearch", "upstream/main"), "hermes-upstream-auditor"),
    # Cross-department completion evidence is owned by the review verifier.
    (("cross-dept handoff audit", "missing steward evidence"), "review-verification-steward"),
    # Research findings are handed to the lab director, who can decompose them
    # into bounded experiments for the producer and runner profiles.
    (("research cycle", "research-scout priority finding", "phase 18"), "lunabot-research-lab-director"),
    # Classifier findings have a deterministic owner instead of parking with
    # the intake router for manual triage.
    (("classifier unknown-handler", "classifier gap", "unclassified"), "content-classifier"),
    (("model spend rebalance", "profile/group budget", "budget over 7d"), "resource-scheduler"),
    (("circular dependency", "approval bottleneck"), "operations-steward"),
    (("live-trading", "paper-safety", "paper-safety", "broker safety"), "paper-safety-guardian"),
    (("alpaca", "broker credential", "broker validation"), "coding-expert"),
    (("dashboard.secret", "state.db", "retired-wal", "gateway restart"), "hermes-maintenance-steward"),
    (_CRON_PURPOSE_TERMS, "hermes-maintenance-steward"),
    (("dependency skew", "missing/bounds unverified"), "dependency-tooling-health-sentinel"),
    (("rnd-", "resolve rnd", "adversarial-fuzz", "fuzz test", "permutation"), "rnd-adversarial-tester"),
)


def repair_profile_for_task(title: Optional[str], body: Optional[str]) -> Optional[str]:
    """Return the specialist profile for known repair scopes.

    Matching is intentionally conservative: an unknown task stays in triage
    for explicit scope rather than letting the orchestrator perform work.
    """
    title = (title or "").strip().casefold()
    text = f"{title}\n{body or ''}".casefold()
    # This title owns a budget review, even when its inventory quotes unrelated
    # stewardship jobs or their old quota failures. It grants no execution rights.
    if re.match(r"^model spend rebalance(?:\s*[:—–-]|\s*$)", title):
        return "resource-scheduler"
    for pattern, profile in _TITLE_INTAKE_RULES:
        if re.match(pattern, title):
            masked_title = _mask_inline_examples(title)
            delimiter = re.search(r":|[—–]|[ \t]+-[ \t]+", masked_title)
            title_clause = masked_title[delimiter.end():] if delimiter else masked_title
            title_clause = re.sub(r"[—–]|[ \t]+-[ \t]+", "\n", title_clause)
            if (_has_operational_blocker(_retained_operational_segments(title_clause))
                    or _has_operational_blocker(
                        _retained_operational_segments(_mask_inline_examples(body or ""))
                    )):
                return "hermes-maintenance-steward"
            return profile
    # A cron profile describes the evidence producer, not a scheduler defect.
    cron_title = re.search(r"\bcron\b(?!\W+profiles?\b)", title)
    for needles, profile in _REPAIR_RULES:
        pattern = _TITLE_REPAIR_RULES.get(profile)
        if (any(needle in text for needle in needles)
                or (pattern and re.match(pattern, title))
                or (needles == _CRON_PURPOSE_TERMS and cron_title)):
            return profile
    return None
