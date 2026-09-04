"""Model-free deterministic execution primitives for Revenue Lab."""

from .catalog import DETERMINISTIC_OPERATIONS, require_no_llm

__all__ = ["DETERMINISTIC_OPERATIONS", "require_no_llm"]
