"""Plain routing records need no per-character quote/code span indexes."""

import pytest

from hermes_cli import kanban_repair_routing as routing


@pytest.mark.parametrize("value", [
    "", "There is no evidence that the automation credential is missing. " * 2_000,
    "Status: automation credential is missing.", "closing characters only: ’ and ”",
], ids=["empty", "repeated-negative", "positive", "closing-only"])
def test_plain_records_do_not_build_unused_inline_indexes(monkeypatch, value):
    def unused_index(*args):
        pytest.fail("plain records must not build quote/code span indexes")

    monkeypatch.setattr(routing, "_inline_markers", unused_index)
    monkeypatch.setattr(routing, "_example_context", unused_index)
    assert routing._mask_inline_line(value) == value
    assert routing._paired_quote_ranges(value) == []


@pytest.mark.parametrize("body, expected", [
    ("There is no evidence that the automation credential is missing.", "dependency-tooling-health-sentinel"),
    ("Status: automation credential is missing.", "hermes-maintenance-steward"),
    ("Example text: “automation credential is missing”.", "dependency-tooling-health-sentinel"),
    ("The workflow report says “automation credential is missing”.", "hermes-maintenance-steward"),
])
def test_plain_and_marked_records_preserve_routing(body, expected):
    assert routing.repair_profile_for_task("GitHub Actions pin drift: inspect dependencies", body) == expected
