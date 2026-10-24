"""Deterministic handoff from Task Orchestrator to repair profiles."""

from __future__ import annotations

import re
from array import array
from typing import Iterator, Optional


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

_INVENTORY_LABEL = re.compile(
    r"(?:^|(?<=[.!?]))\s*(?:[-*+] )?(?:inventory|quoted(?:\s+inventory)?)\s*:", re.IGNORECASE,
)
_FENCE_OPEN = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_FENCE_CLOSE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*$")
_BLOCKQUOTE = re.compile(r"^\s{0,3}>")
_BLOCKQUOTE_EMPTY = re.compile(r"^\s{0,3}>\s*$")
_INLINE_QUOTES = {"'": "'", '"': '"', "‘": "’", "“": "”"}
_INLINE_OPENERS = (*_INLINE_QUOTES, "`")
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


def _inline_markers(line: str) -> tuple[dict[str, list[int]], dict[int, list[int]]]:
    """Index quote closers and code runs once per line."""
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
    """Index cues and next-content positions for linear span checks."""
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
    """Check adjacent example cues without growing-prefix or suffix scans."""
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
    """Mask only explicitly example-cued paired spans on one line."""
    if not any(marker in line for marker in _INLINE_OPENERS):
        return line
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
    """Mask explicitly example-cued paired spans while preserving line breaks."""
    masked: list[str] = []
    for chunk in value.splitlines(keepends=True):
        line = chunk.rstrip("\r\n")
        masked.append(_mask_inline_line(line))
        masked.append(chunk[len(line):])
    return "".join(masked)


def _retained_operational_segments(value: str) -> list[str]:
    """Return hard-separated retained paragraphs with their soft newlines."""
    paragraphs: list[str] = []
    current: list[str] = []
    inventory = False
    inventory_has_content = False
    quoted_paragraph = False
    fence: tuple[str, int] | None = None
    pending_label = False

    def flush() -> None:
        if current:
            paragraphs.append("\n".join(current))
            current.clear()

    for line in value.splitlines():
        if fence:
            if _fence_closes(line, fence):
                fence = None
                flush()
            continue
        opening = _FENCE_OPEN.match(line)
        if opening:
            flush()
            pending_label = False
            if inventory:
                inventory_has_content = True
            marker = opening.group(1)
            fence = (marker[0], len(marker))
            continue

        if inventory:
            if line.strip():
                inventory_has_content = True
            elif inventory_has_content:
                inventory = False
                inventory_has_content = False
                flush()
            continue

        if _BLOCKQUOTE_EMPTY.match(line):
            quoted_paragraph = False
            pending_label = False
            flush()
            continue
        if quoted_paragraph:
            if not line.strip():
                quoted_paragraph = False
                pending_label = False
                flush()
            continue
        if _BLOCKQUOTE.match(line):
            flush()
            pending_label = False
            quoted_paragraph = True
            continue

        label = _INVENTORY_LABEL.search(line)
        if label:
            prefix = line[:label.start()].strip()
            if prefix:
                current.append(prefix)
                flush()
            pending_label = False
            inventory = True
            inventory_has_content = bool(line[label.end():].strip())
            continue

        if line.strip():
            current.append(line)
            pending_label = line.rstrip().endswith(":")
        elif pending_label and current:
            current.append("")
        else:
            pending_label = False
            flush()
    flush()
    return paragraphs


_CREDENTIAL = r"(?:the )?automation credential is missing"
_GH_AUTH = (
    r"(?:the )?gh auth failure"
    r"(?: (?:stopped|blocked|prevented) the workflow(?: retry)?(?: from running)?)?"
)
_LEGACY_MARKER = (
    r"(?:the )?(?:missing hermes_cli|audit-pr unavailable|inspect-pr unavailable|"
    r"audit_deferred|untrusted receipt)"
)
_QUOTA = (
    r"(?:the )?(?:(?:workflow|provider|account|automation) )?quota "
    r"(?:is|was|has been) (?:exhausted|exceeded)"
)
_USAGE = (
    r"(?:the )?(?:(?:workflow|provider|account|automation) )?usage limits "
    r"(?:are|were|have been) (?:exceeded|exhausted)"
)
_QUOTA_IMPACT = (
    r"(?:the )?(?:(?:workflow|provider|account|automation) )?(?:"
    r"quota failures? (?:are|were|is|was)? ?(?:blocking|blocked|preventing|prevented|failing) "
    r"(?:the )?workflow(?: retry)?(?: from running)?|"
    r"quota (?:stopped|blocked|prevented) the workflow(?: retry)?(?: from running)?|"
    r"quota failed|"
    r"usage limits failed|usage limits (?:blocked|prevented) "
    r"the workflow(?: retry)?(?: from running)?)"
)
_PROVIDER_RETURN = r"(?:the )?provider returned usage limits"
_USAGE_EVENT = (
    r"(?:the )?(?:provider|workflow|automation|account) (?:(?:has|have|had) )?"
    r"(?:hit|encountered|reached|returned|exceeded|exhausted) usage limits"
)
_USAGE_CAUSE = (
    r"(?:the )?(?:provider|workflow|automation|account) (?:(?:has|have|had) )?"
    r"(?:failed|cannot run|can not run|can't run|can’t run|(?:is|are|was|were) blocked) "
    r"(?:due to|because of|from) (?:the )?usage limits"
)
_DIAGNOSTIC = (
    r"(?:the )?lunabot diagnostics? (?:failed|(?:has|have) failed|"
    r"(?:is|are|was|were) (?:blocked|unavailable)|"
    r"(?:returned|reported|encountered) (?:an )?error)"
)
_P = "(?:" + "|".join((
    _CREDENTIAL, _GH_AUTH, _LEGACY_MARKER, _PROVIDER_RETURN, _QUOTA,
    _USAGE, _QUOTA_IMPACT, _USAGE_EVENT, _USAGE_CAUSE, _DIAGNOSTIC,
)) + ")"
# Reports often keep the sentence-ending period/exclamation mark inside their
# quotation marks. Accept that punctuation only in the quoted positive record;
# the enclosing report/causal frame and full-record match remain mandatory.
_QUOTED_ENDING = r"[.!]?"
_QUOTED_P = (
    r"(?:'" + _P + _QUOTED_ENDING + r"'|\"" + _P + _QUOTED_ENDING + r"\"|"
    r"‘" + _P + _QUOTED_ENDING + r"’|“" + _P + _QUOTED_ENDING + r"”|"
    r"(?P<code_ticks>`+)" + _P + r"(?P=code_ticks))"
)
_ALLOWED_TAIL = (
    r"(?: (?:for this account|for the automation account|during the run|"
    r"while collecting replay data|before LunaBot replay|"
    r"before comparing LunaBot replay ordering)|"
    r", so the workflow (?:cannot|can't) run)?"
)
_P_WITH_TAIL = _P + _ALLOWED_TAIL
_QUOTED_P_WITH_TAIL = _QUOTED_P + _ALLOWED_TAIL
_DIRECT_PREFIX = r"(?:current |later, )?"
_BULLET_PREFIX = r"(?:[-*+] )?"
_STATUS_FRAME = r"(?:status|error|failure|report|operational blocker|later report): "
_REPORT_FRAME = (
    r"(?:(?:the )?workflow report says|an operator reported that|"
    r"there is evidence that|we found an indication that) "
)
_CAUSAL_FRAME = (
    r"(?:the )?(?:workflow|deployment) "
    r"(?:failed|cannot run|can't run|can’t run|didn't run|didn’t run|did not run) ?,? "
    r"(?:because of|because|due to|when) "
)
_QUOTED_REPORT_PREFIXES = (
    "the workflow report says ", "workflow report says ",
    "an operator reported that ", "there is evidence that ",
    "we found an indication that ",
)
_QUOTED_CAUSAL_PREFIXES = ("the workflow ", "workflow ", "the deployment ", "deployment ")


def _paired_quote_ranges(line: str) -> list[tuple[int, int]]:
    """Return balanced same-line quote spans using the shared marker indexes."""
    if not any(marker in line for marker in _INLINE_OPENERS):
        return []
    quote_closers, code_runs = _inline_markers(line)
    ranges: list[tuple[int, int]] = []
    used: dict[str, int] = dict.fromkeys(quote_closers, 0)
    code_used: dict[int, int] = dict.fromkeys(code_runs, 0)
    index = 0
    while index < len(line):
        if line[index] == "`":
            end = index + 1
            while end < len(line) and line[end] == "`":
                end += 1
            length = end - index
            runs = code_runs.get(length, [])
            cursor = code_used[length]
            while cursor < len(runs) and runs[cursor] <= index:
                cursor += 1
            code_used[length] = cursor
            if cursor < len(runs):
                close = runs[cursor]
                ranges.append((index, close + length - 1))
                index = close + length
                continue
            index = end
            continue
        closing = _INLINE_QUOTES.get(line[index])
        is_open = closing is not None
        if line[index] == "'" and index > 0 and (line[index - 1].isalnum() or line[index - 1] == "_"):
            is_open = False
        if is_open:
            closers = quote_closers[closing]
            cursor = used[closing]
            while cursor < len(closers) and closers[cursor] <= index:
                cursor += 1
            used[closing] = cursor
            if cursor < len(closers):
                close = closers[cursor]
                ranges.append((index, close))
                index = close + 1
                continue
        index += 1
    return ranges


def _assertion_units(value: str) -> Iterator[str]:
    """Split complete sentence records outside balanced same-line spans."""
    pending: list[str] = []
    pending_is_list_item = False
    for line in value.split("\n"):
        bullet_prefix = line[:5].lstrip(" ")
        is_list_item = (
            len(line[:5]) - len(bullet_prefix) <= 3
            and bullet_prefix.startswith(("- ", "* ", "+ "))
        )
        if is_list_item and pending_is_list_item:
            unit = " ".join(part for part in pending if part.strip())
            if unit.strip():
                yield unit
            pending.clear()
            pending_is_list_item = False
        if is_list_item and not any(part.strip() for part in pending):
            pending_is_list_item = True
        ranges = _paired_quote_ranges(line)
        range_index = 0
        start = 0
        index = 0
        while index < len(line):
            if range_index < len(ranges) and index == ranges[range_index][0]:
                index = ranges[range_index][1] + 1
                range_index += 1
                continue
            if line[index] in ".!?" and (index + 1 == len(line) or line[index + 1].isspace()):
                pending.append(line[start:index + 1])
                unit = " ".join(part for part in pending if part.strip())
                if unit.strip():
                    yield unit
                pending.clear()
                index += 1
                while index < len(line) and line[index].isspace():
                    index += 1
                start = index
                continue
            index += 1
        pending.append(line[start:])
    if pending:
        unit = " ".join(part for part in pending if part.strip())
        if unit.strip():
            yield unit


def _normalize_record(value: str) -> str:
    """Normalize soft whitespace without removing opaque example barriers."""
    return " ".join(value.casefold().split())


def _is_single_positive(value: str) -> bool:
    """Match one direct, field, report, or causal positive record."""
    value = _normalize_record(value)
    if value.endswith("?") or _INLINE_BARRIER in value:
        return False
    value = value.rstrip(".!")
    if re.fullmatch(_BULLET_PREFIX + _DIRECT_PREFIX + _P_WITH_TAIL, value, re.IGNORECASE):
        return True
    if re.fullmatch(_BULLET_PREFIX + _STATUS_FRAME + _P_WITH_TAIL, value, re.IGNORECASE):
        return True
    candidate = value[2:] if value.startswith(("- ", "* ", "+ ")) else value
    if candidate.startswith(_QUOTED_REPORT_PREFIXES):
        if re.fullmatch(_BULLET_PREFIX + _REPORT_FRAME + _QUOTED_P_WITH_TAIL, value, re.IGNORECASE):
            return True
    if re.fullmatch(_BULLET_PREFIX + _REPORT_FRAME + _P_WITH_TAIL, value, re.IGNORECASE):
        return True
    if candidate.startswith(_QUOTED_CAUSAL_PREFIXES):
        if re.fullmatch(_BULLET_PREFIX + _CAUSAL_FRAME + _QUOTED_P_WITH_TAIL, value, re.IGNORECASE):
            return True
    return re.fullmatch(_BULLET_PREFIX + _CAUSAL_FRAME + _P_WITH_TAIL, value, re.IGNORECASE) is not None


def _is_positive_operational_record(value: str) -> bool:
    """Require full affirmative consumption of a record or approved compound."""
    normalized = _normalize_record(value)
    pieces = [piece.strip() for piece in normalized.split(";")]
    if len(pieces) == 1:
        return _is_single_positive(pieces[0])
    if len(pieces) == 2 and re.fullmatch(
        r"(?:the )?(?:workflow|deployment) "
        r"(?:failed|cannot run|can't run|can’t run|didn't run|didn’t run|did not run)",
        pieces[0].rstrip(".!"), re.IGNORECASE,
    ):
        return _is_single_positive(pieces[1])
    if len(pieces) > 1:
        if not all(_is_single_positive(piece) for piece in pieces[:-1]):
            return False
        final = pieces[-1].strip()
        if final == "why?":
            return True
        if final.endswith("?"):
            return False
        final = final.rstrip(".! ")
        return _is_single_positive(final) or final == "retry is blocked"
    return False


def _has_operational_blocker(segments: list[str]) -> bool:
    """Accept any complete positive record in independently retained paragraphs."""
    for segment in segments:
        for unit in _assertion_units(_mask_inline_examples(segment)):
            if _is_positive_operational_record(unit):
                return True
    return False


def _title_has_operational_blocker(title: str, pattern: str) -> bool:
    """Recognize only title evidence following the family’s defined frame."""
    normalized = title.casefold()
    if normalized.startswith("diagnostic experiment:"):
        rest = normalized[len("diagnostic experiment:"):].lstrip()
        suffix = _diagnostic_title_suffix(rest)
    else:
        match = re.match(pattern, normalized)
        if not match:
            return False
        suffix = normalized[match.end():]
    return _has_operational_blocker(_retained_operational_segments(suffix))


def _diagnostic_title_suffix(value: str) -> str:
    """Slice the explicit compare/replay dash clause in one forward pass."""
    prefix = "compare lunabot replay"
    if not value.startswith(prefix):
        return value
    index = len(prefix)
    if index == len(value) or not value[index].isspace():
        return value
    while index < len(value) and value[index].isspace():
        index += 1
    for word in ("ordering", "order"):
        end = index + len(word)
        if value.startswith(word, index) and (end == len(value) or value[end].isspace()):
            index = end
            while index < len(value) and value[index].isspace():
                index += 1
            break
    if index < len(value) and value[index] in "—–-":
        return value[index + 1:].lstrip()
    return value

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
            if (_title_has_operational_blocker(title, pattern)
                    or _has_operational_blocker(_retained_operational_segments(body or ""))):
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
