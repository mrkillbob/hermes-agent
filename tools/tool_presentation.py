"""Execution-owned presentation of an explicitly frozen editor-only catalog.

This is a schema presentation choice, never tool authorization. The gateway binds
it only after validating the editor manifest; mixed and ordinary sessions clear it.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_frozen_editor_names = ContextVar("frozen_editor_tool_names", default=None)


@contextmanager
def frozen_editor_tool_presentation(names):
    token = _frozen_editor_names.set(tuple(sorted(names)) if names else None)
    try:
        yield
    finally:
        _frozen_editor_names.reset(token)


def current_frozen_editor_tool_names():
    """Immutable cache identity, also used to require an exact selected catalog."""
    return _frozen_editor_names.get()
