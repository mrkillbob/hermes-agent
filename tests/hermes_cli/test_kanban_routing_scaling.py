"""Long inline examples must not stall routing decisions."""

import subprocess
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TITLE = "GitHub Actions pin drift: inspect dependencies"
_OWNER = "dependency-tooling-health-sentinel"


def _assert_child_routes_long_input(payload_expression, title_expression=None):
    title_expression = title_expression or repr(_TITLE)
    script = f"""
from hermes_cli.kanban_repair_routing import repair_profile_for_task
title = {title_expression}
body = {payload_expression}
assert repair_profile_for_task(title, body) == {_OWNER!r}
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_long_unmatched_curly_quote_payload_finishes_promptly():
    _assert_child_routes_long_input("'‘x' * 200_000")


def test_many_paired_example_spans_finish_promptly():
    _assert_child_routes_long_input("'Example text: “x” ' * 200_000")


def test_many_conditional_blocker_mentions_finish_promptly():
    _assert_child_routes_long_input("'Check whether the automation credential is missing. ' * 200_000")


def test_many_no_evidence_mentions_finish_promptly():
    _assert_child_routes_long_input(
        "'There is no evidence that the automation credential is missing. ' * 200_000",
    )


def test_long_unmatched_suffix_whitespace_finishes_promptly():
    _assert_child_routes_long_input("'“x”' + ' ' * 200_000 + ' ordinary review'")


def test_long_title_whitespace_without_a_hyphen_finishes_promptly():
    title = "'GitHub Actions pin drift: ' + ' ' * 200_000 + 'inspect dependencies'"
    _assert_child_routes_long_input("'ordinary review'", title_expression=title)
