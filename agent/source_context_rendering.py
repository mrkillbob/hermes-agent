"""Deterministic discovered-context previews, separate from source authority."""
from pathlib import PurePosixPath


PREVIEW_KIND = 'context_hint_preview_v1:'


def presentation_display_path(segment):
    """The display alias is ordinary scanned text, never source-authorized bytes."""
    kind = segment.presentation_kind
    from agent.source_context_preview import PREVIEW_PROOF_KIND
    for prefix in (PREVIEW_KIND, PREVIEW_PROOF_KIND):
        if kind.startswith(prefix):
            return kind[len(prefix):]
    return ''


def presentation_secret_text(segment):
    return segment.text if presentation_display_path(segment) else ''


def presentation_path_encoding_inputs(segment):
    path = presentation_display_path(segment)
    # Preserve the ordinary scanner's existing slash-word grammar. Padding
    # before a separator otherwise prevents its candidate regex matching the
    # encoded component at all; expose those literal padded components too.
    return (path, *(part for part in path.split('/') if '=' in part)) if path else ()


def hint_relative_path(value):
    from agent.subdirectory_hints import _HINT_FILENAMES

    path = PurePosixPath(value)
    if (path.is_absolute() or '\\' in value or '..' in path.parts
            or any(ord(char) < 32 or char == ']' for char in value)
            or str(path) != value or path.name not in _HINT_FILENAMES):
        return None
    return path


def render_hint_preview(raw_text, relative_path):
    """Reuse the loader's exact normalization and bounded head/tail renderer."""
    from agent.prompt_builder import _truncate_content
    from agent.source_context_presentation import context_display_text
    from agent.subdirectory_hints import _MAX_HINT_CHARS

    relative = hint_relative_path(relative_path)
    if relative is None:
        return None
    return _truncate_content(
        context_display_text(raw_text), relative.name, max_chars=_MAX_HINT_CHARS,
        read_path=relative_path, queue_warning=False,
    )
