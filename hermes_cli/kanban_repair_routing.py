"""Deterministic handoff from Task Orchestrator to repair profiles."""

from __future__ import annotations

import re
from array import array
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
    (r"\b(?:missing hermes_cli|audit-pr unavailable|inspect-pr unavailable|audit_deferred|untrusted receipt)\b",
     "hermes-maintenance-steward"),
    (r"\b(?:the )?provider returned usage limits\b",
     "hermes-maintenance-steward"),
    (r"\b(?:the )?(?:(?:workflow|provider|account|automation) )?(?:"
     r"quota (?:is|was|has been) (?:exhausted|exceeded)|"
     r"quota failures? (?:are|were|is|was)? ?(?:blocking|blocked|preventing|prevented|failing)|"
     r"usage limits (?:are|were|have been) (?:exceeded|exhausted)|"
     r"(?:quota|usage limits) (?:blocked|prevented|failed))\b",
     "hermes-maintenance-steward"),
    (r"\b(?:the )?(?:provider|workflow|automation|account) "
     r"(?:(?:has|have|had) )?(?:hit|encountered|reached|returned|exceeded|exhausted) usage limits\b",
     "hermes-maintenance-steward"),
    (r"\b(?:the )?(?:provider|workflow|automation|account) "
     r"(?:(?:has|have|had) )?(?:failed|cannot run|can not run|can't run|(?:is|are|was|were) blocked) "
     r"(?:due to|because of|from) (?:the )?usage limits\b",
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
_BLOCKER_NEGATION = re.compile(
    r"\b(?:no(?:\s+(?:evidence|indication)\s+that)?|if|whether|unless)\b", re.IGNORECASE,
)
_WHEN_INQUIRY = re.compile(r"\bwhen\b", re.IGNORECASE)
_INQUIRY_VERB = re.compile(r"\b(?:check|review|record|inspect|determine|verify)\s*", re.IGNORECASE)
_INLINE_QUOTES = {"'": "'", '"': '"', "‘": "’", "“": "”"}
_INLINE_BARRIER = "\ue000"
_EXAMPLE_CUE = re.compile(
    r"(?:\bexample(?:\s+text)?\s*[:,-]?|\bfor example\s*,?|\bcompare the phrase)",
    re.IGNORECASE,
)
_EXAMPLE_SUFFIX = re.compile(r"is an example sentence\b", re.IGNORECASE)


def _fence_closes(line: str, marker: tuple[str, int]) -> bool:
    """Whether a Markdown fence line closes the active fence."""
    match = _FENCE_CLOSE.match(line)
    return bool(match and match.group(1)[0] == marker[0] and len(match.group(1)) >= marker[1])


def _title_delimiter_end(title: str) -> int | None:
    """Find the first title clause delimiter with a single forward scan."""
    index = 0
    while index < len(title):
        if title[index] in ":—–":
            return index + 1
        if title[index] not in " \t":
            index += 1
            continue
        while index < len(title) and title[index] in " \t":
            index += 1
        if index < len(title) and title[index] == "-" and index + 1 < len(title) and title[index + 1] in " \t":
            index += 1
            while index < len(title) and title[index] in " \t":
                index += 1
            return index
    return None


def _normalize_title_dash_clauses(value: str) -> str:
    """Replace title-only dash clause separators without regex rescans."""
    parts: list[str] = []
    chunk_start = 0
    index = 0
    while index < len(value):
        if value[index] in "—–":
            parts.extend((value[chunk_start:index], "\n"))
            index += 1
            chunk_start = index
            continue
        if value[index] not in " \t":
            index += 1
            continue
        separator_start = index
        while index < len(value) and value[index] in " \t":
            index += 1
        if index < len(value) and value[index] == "-" and index + 1 < len(value) and value[index + 1] in " \t":
            index += 1
            while index < len(value) and value[index] in " \t":
                index += 1
            parts.extend((value[chunk_start:separator_start], "\n"))
            chunk_start = index
    parts.append(value[chunk_start:])
    return "".join(parts)


def _inline_markers(line: str) -> tuple[dict[str, list[int]], dict[int, list[int]]]:
    """Index eligible quote closers and code runs once per line."""
    quote_closers = {quote: [] for quote in set(_INLINE_QUOTES.values())}
    index = 0
    while index < len(line):
        if line[index] == "\\":
            index += 2
            continue
        if (line[index] in quote_closers
                and not (line[index] in {"'", "’"} and index > 0
                         and index + 1 < len(line)
                         and line[index - 1].isalnum() and line[index + 1].isalnum())):
            quote_closers[line[index]].append(index)
        index += 1

    code_runs: dict[int, list[int]] = {}
    index = 0
    while index < len(line):
        if line[index] != "`":
            index += 1
            continue
        end = index + 1
        while end < len(line) and line[end] == "`":
            end += 1
        code_runs.setdefault(end - index, []).append(index)
        index = end
    return quote_closers, code_runs


def _example_context(line: str) -> tuple[list[int], list[int], array]:
    """Index cues and next-content positions for constant-time span checks."""
    cue_ends = [match.end() for match in _EXAMPLE_CUE.finditer(line)]
    suffix_starts = [match.start() for match in _EXAMPLE_SUFFIX.finditer(line)]
    next_content = array("I", [len(line)]) * (len(line) + 1)
    for index in range(len(line) - 1, -1, -1):
        next_content[index] = index if not line[index].isspace() else next_content[index + 1]
    return cue_ends, suffix_starts, next_content


def _span_is_example(
    start: int, end: int, cue_ends: list[int], suffix_starts: list[int], next_content: list[int],
    cue_cursor: int, suffix_cursor: int,
) -> tuple[bool, int, int]:
    """Check adjacent example cues without copying or rescanning line prefixes."""
    while cue_cursor < len(cue_ends) and cue_ends[cue_cursor] <= start:
        cue_cursor += 1
    if cue_cursor and next_content[cue_ends[cue_cursor - 1]] >= start:
        return True, cue_cursor, suffix_cursor
    while suffix_cursor < len(suffix_starts) and suffix_starts[suffix_cursor] < end + 1:
        suffix_cursor += 1
    if (suffix_cursor < len(suffix_starts) and end + 1 < suffix_starts[suffix_cursor]
            and next_content[end + 1] == suffix_starts[suffix_cursor]):
        return True, cue_cursor, suffix_cursor
    return False, cue_cursor, suffix_cursor


def _mask_inline_line(line: str) -> str:
    """Mask only explicitly example-cued spans on one line."""
    quote_closers, code_runs = _inline_markers(line)
    cue_ends, suffix_starts, next_content = _example_context(line)
    quote_cursors = dict.fromkeys(quote_closers, 0)
    code_cursors = dict.fromkeys(code_runs, 0)
    cue_cursor = 0
    suffix_cursor = 0
    masked: list[str] = []
    index = 0
    while index < len(line):
        if line[index] == "`":
            end_run = index + 1
            while end_run < len(line) and line[end_run] == "`":
                end_run += 1
            run_length = end_run - index
            runs = code_runs.get(run_length, [])
            cursor = code_cursors.get(run_length, 0)
            while cursor < len(runs) and runs[cursor] <= index:
                cursor += 1
            code_cursors[run_length] = cursor
            if run_length < 3 and cursor < len(runs):
                close = runs[cursor]
                is_example, cue_cursor, suffix_cursor = _span_is_example(
                    index, close + run_length - 1, cue_ends, suffix_starts, next_content,
                    cue_cursor, suffix_cursor,
                )
                masked.append(_INLINE_BARRIER if is_example else line[index:close + run_length])
                index = close + run_length
                continue
            masked.append(line[index:end_run])
            index = end_run
            continue

        closing = _INLINE_QUOTES.get(line[index])
        opens = closing is not None
        if line[index] == "'" and index > 0 and (line[index - 1].isalnum() or line[index - 1] == "_"):
            opens = False
        if opens:
            closers = quote_closers[closing]
            cursor = quote_cursors[closing]
            while cursor < len(closers) and closers[cursor] <= index:
                cursor += 1
            quote_cursors[closing] = cursor
            if cursor < len(closers):
                close = closers[cursor]
                is_example, cue_cursor, suffix_cursor = _span_is_example(
                    index, close, cue_ends, suffix_starts, next_content, cue_cursor, suffix_cursor,
                )
                masked.append(_INLINE_BARRIER if is_example else line[index:close + 1])
                index = close + 1
                continue
        masked.append(line[index])
        index += 1
    return "".join(masked)


def _mask_inline_examples(value: str) -> str:
    """Mask only explicitly example-cued inline spans, retaining line breaks."""
    masked: list[str] = []
    for chunk in value.splitlines(keepends=True):
        line = chunk.rstrip("\r\n")
        masked.append(_mask_inline_line(line))
        masked.append(chunk[len(line):])
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


def _context_prefixes(value: str, trigger_pattern: re.Pattern[str]) -> dict[int, int]:
    """Index accepted trigger endpoints, including optional article/quote paths."""
    endpoints: dict[int, int] = {}
    for trigger in trigger_pattern.finditer(value):
        word_end = trigger.end()
        cursor = word_end
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        if cursor == word_end:
            continue
        endpoints[cursor] = trigger.start()
        paths = [cursor]
        if (value.startswith("the", cursor)
                and (cursor + 3 == len(value) or not value[cursor + 3].isalnum())):
            article_end = cursor + 3
            if article_end < len(value) and value[article_end].isspace():
                cursor = article_end
                while cursor < len(value) and value[cursor].isspace():
                    cursor += 1
                endpoints[cursor] = trigger.start()
                paths.append(cursor)
        for path in paths:
            cursor = path
            while cursor < len(value) and value[cursor] in "`'\"‘“":
                cursor += 1
                while cursor < len(value) and value[cursor].isspace():
                    cursor += 1
                endpoints[cursor] = trigger.start()
    return endpoints


def _has_operational_blocker(segments: list[str]) -> bool:
    """Find concrete blockers while skipping immediate negative conditions."""
    for segment in segments:
        normalized = segment.casefold()
        negation_ends = _context_prefixes(normalized, _BLOCKER_NEGATION)
        when_starts = _context_prefixes(normalized, _WHEN_INQUIRY)
        inquiry_ends = {match.end() for match in _INQUIRY_VERB.finditer(normalized)}
        first_content = next(
            (index for index, char in enumerate(normalized) if not char.isspace()), len(normalized),
        )
        for pattern, _profile in _OPERATIONAL_BLOCKER_RULES:
            for match in re.finditer(pattern, normalized):
                if match.start() in negation_ends:
                    continue
                when_start = when_starts.get(match.start())
                if (when_start is not None
                        and (when_start <= first_content or when_start in inquiry_ends)):
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
            delimiter_end = _title_delimiter_end(masked_title)
            title_clause = masked_title[delimiter_end:] if delimiter_end is not None else masked_title
            title_clause = _normalize_title_dash_clauses(title_clause)
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
