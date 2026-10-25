"""The retained bundled tracing plugin must remain selectable through PM."""
from pathlib import Path

from pm.extras import _anchors
from pm.features import declared_extras


def test_bundled_langfuse_is_selectable_through_pm():
    root = Path(__file__).resolve().parents[2]
    assert "langfuse" in declared_extras(root), "Bundled Langfuse extra is not selectable"
    assert "langfuse" in _anchors("langfuse")
