"""Closed public context-blocking notices; never authority for the rejected source."""
import re

from tools.threat_patterns import INVISIBLE_CHARS, _PATTERNS

NOTICE_KIND = 'context_blocked_notice_v1'
CAPACITY_KIND = 'context_capacity_notice_v1'
_FILENAMES = ('AGENTS.override.md', 'AGENTS.md', 'agents.md', 'CLAUDE.md', 'claude.md', '.cursorrules')
_FINDINGS = frozenset(
    [name for _, name, scope in _PATTERNS if scope in ('all', 'context')]
    + [f'invisible_unicode_U+{ord(char):04X}' for char in INVISIBLE_CHARS]
)
_NOTICE = re.compile(
    r'\[BLOCKED: (' + '|'.join(re.escape(name) for name in _FILENAMES)
    + r') contained potential prompt injection \(([^\n()]+)\)\. Content not loaded\.\]'
)

_CAPACITY = re.compile(
    r'\[UNAVAILABLE: (' + '|'.join(re.escape(name) for name in _FILENAMES)
    + r') exceeds protected source (byte|line) limit\. No guidance preview was loaded\. '
    r'Use a supported bounded read_file slice or provide a bounded guidance file before relying on these instructions\.\]'
)


def capacity_notice_filename(text):
    match = _CAPACITY.fullmatch(text) if isinstance(text, str) else None
    return match[1] if match else None


def capacity_notice(filename, reason):
    text = (f'[UNAVAILABLE: {filename} exceeds protected source {reason} limit. '
            'No guidance preview was loaded. Use a supported bounded read_file slice or provide a bounded guidance file '
            'before relying on these instructions.]')
    return text if capacity_notice_filename(text) is not None else None


def blocked_notice_filename(text):
    """Recognize only fixed public vocabulary, with no free prose or payload slot."""
    if not isinstance(text, str) or len(text) > 4096:
        return None
    match = _NOTICE.fullmatch(text)
    if match is None:
        return None
    findings = match[2].split(', ')
    if (not 0 < len(findings) <= len(_FINDINGS) or len(set(findings)) != len(findings)
            or any(finding not in _FINDINGS for finding in findings)):
        return None
    return match[1]


def context_notice_piece(remaining, header_match):
    """This path follows a verified read prefix; the notice grants no source privilege."""
    from agent.source_context_presentation import _HEADER
    from agent.source_context_rendering import hint_relative_path

    relative = hint_relative_path(header_match[1])
    if relative is None:
        return None
    body_start = header_match.end()
    following = _HEADER.search(remaining, body_start)
    end = following.start() if following is not None else len(remaining)
    body = remaining[body_start:end]
    if blocked_notice_filename(body) == relative.name:
        return body, end, NOTICE_KIND
    if capacity_notice_filename(body) == relative.name:
        return body, end, CAPACITY_KIND
    return None
