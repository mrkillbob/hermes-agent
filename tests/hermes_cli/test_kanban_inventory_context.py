"""Quoted inventory context must not turn examples into operational blockers."""

import pytest

from hermes_cli.kanban_repair_routing import repair_profile_for_task


INTAKE_TITLES = [
    ("Vite lock stale: inspect dependencies", "dependency-tooling-health-sentinel"),
    ("Regenerate Python lock — inspect dependencies", "dependency-tooling-health-sentinel"),
    ("Local tooling refresh – inspect dependencies", "dependency-tooling-health-sentinel"),
    ("GitHub Actions pin drift - inspect dependencies", "dependency-tooling-health-sentinel"),
    ("Diagnostic experiment: compare LunaBot replay ordering", "research-experiment-runner"),
]

BLOCKERS = [
    "Automation credential is missing",
    "GH auth failure stopped the workflow",
    "Provider returned usage limits for this account",
    "Workflow quota is exhausted",
    "Usage limits blocked the workflow",
    "Quota prevented the workflow from running",
    "LunaBot diagnostics failed",
    "LunaBot diagnostic has failed",
    "LunaBot diagnostics returned an error",
]


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("blocker", BLOCKERS)
def test_multiline_inventory_label_quotes_each_direct_blocker(title, owner, blocker):
    body = f"Inventory:\n{blocker} in the example card."
    assert repair_profile_for_task(title, body) == owner


@pytest.mark.parametrize("label", ["Inventory:", "Quoted:", "Quoted inventory:"])
@pytest.mark.parametrize("blocker", BLOCKERS)
def test_label_only_quote_blocks_survive_indentation_and_leading_blank_lines(label, blocker):
    body = f"\n\n  {label}\n\n    {blocker} in the example card.\n    More example details."
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == INTAKE_TITLES[0][1]


@pytest.mark.parametrize("label", ["Inventory:", "Quoted:", "Quoted inventory:"])
def test_inline_label_and_continued_lines_remain_quoted(label):
    body = f"Assessment notes. {label} Automation credential is missing\ncontinued example detail."
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == INTAKE_TITLES[0][1]


def test_inventory_label_after_sentence_boundary_and_indented_label_are_quoted():
    body = "Ordinary assessment.\n    Inventory:\n    Usage limits were exceeded in the sample."
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == INTAKE_TITLES[0][1]


def test_markdown_blockquote_paragraph_includes_lazy_continuation():
    body = "> Example card:\n  Automation credential is missing\n  and its workflow is blocked.\n\nAssessment continues here."
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == INTAKE_TITLES[0][1]


def test_empty_blockquote_marker_ends_quote_before_actual_blocker():
    body = "> Old example:\n> Automation credential is missing.\n>\nUsage limits were exceeded."
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == "hermes-maintenance-steward"


def test_initial_empty_blockquote_marker_does_not_quote_following_body():
    body = ">\nUsage limits were exceeded."
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == "hermes-maintenance-steward"


@pytest.mark.parametrize(("opening", "closing"), [
    ("```text", "````"),
    ("~~~", "~~~~~"),
])
def test_matching_markdown_fences_remain_quoted_across_blank_lines(opening, closing):
    body = f"{opening}\n\nAutomation credential is missing.\n\n{closing}\n"
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == INTAKE_TITLES[0][1]


@pytest.mark.parametrize("opening", ["```text", "~~~yaml"])
def test_unclosed_markdown_fence_remains_quoted(opening):
    body = f"{opening}\nAutomation credential is missing.\n\nStill inside example."
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == INTAKE_TITLES[0][1]


def test_shorter_backtick_run_does_not_close_longer_fence():
    body = "````text\n```\nAutomation credential is missing.\n````"
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == INTAKE_TITLES[0][1]


def test_different_fence_character_does_not_close_open_fence():
    body = "```text\n~~~\nAutomation credential is missing.\n```"
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == INTAKE_TITLES[0][1]


def test_inventory_label_and_fence_keep_blank_fenced_content_quoted():
    body = "Inventory:\n\n```text\n\nAutomation credential is missing.\n\n```"
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == INTAKE_TITLES[0][1]


def test_actual_blocker_after_closed_fence_remains_visible():
    body = "```text\nAutomation credential is missing.\n```\n\nUsage limits were exceeded."
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == "hermes-maintenance-steward"


def test_actual_blockers_before_and_after_completed_inventory_remain_visible():
    before = "Usage limits were exceeded.\nInventory:\nAutomation credential is missing in the example."
    after = "Inventory:\nAutomation credential is missing in the example.\n\nUsage limits were exceeded."
    for body in (before, after):
        assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == "hermes-maintenance-steward"


def test_blocker_after_label_only_inventory_and_ending_blank_remains_visible():
    body = "\n\nInventory:\n\nAutomation credential is missing in the example.\n\nUsage limits were exceeded."
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == "hermes-maintenance-steward"


def test_ordinary_intro_then_direct_blocker_remains_visible():
    body = "Assessment: the task concerns dependency health.\nUsage limits were exceeded."
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == "hermes-maintenance-steward"


@pytest.mark.parametrize("title", [
    "Vite lock stale: Automation credential is missing",
    "Regenerate Python lock — Automation credential is missing",
    "Local tooling refresh – Automation credential is missing",
    "GitHub Actions pin drift - Automation credential is missing",
    "Diagnostic experiment: Automation credential is missing before LunaBot replay",
])
def test_title_clause_after_first_family_delimiter_is_inspected(title):
    assert repair_profile_for_task(title, None) == "hermes-maintenance-steward"


def test_diagnostic_family_greedy_match_does_not_skip_earlier_title_clause():
    title = "Diagnostic experiment: Usage limits were exceeded before comparing LunaBot replay ordering"
    assert repair_profile_for_task(title, None) == "hermes-maintenance-steward"


def test_direct_blocker_in_later_title_clause_after_em_dash_is_inspected():
    title = "Diagnostic experiment: compare LunaBot replay — quota is exhausted"
    assert repair_profile_for_task(title, None) == "hermes-maintenance-steward"


def test_title_inventory_context_carries_across_later_dash_clause():
    title = "GitHub Actions pin drift: Inventory: example — Automation credential is missing"
    assert repair_profile_for_task(title, None) == "dependency-tooling-health-sentinel"


def test_title_inventory_label_after_inspect_clause_carries_across_dashes():
    title = "GitHub Actions pin drift: inspect — Inventory: example — Automation credential is missing"
    assert repair_profile_for_task(title, None) == "dependency-tooling-health-sentinel"


def test_title_hyphen_clause_after_blank_line_survives_inventory_context():
    title = "GitHub Actions pin drift: Inventory: old example\n\n - Automation credential is missing"
    assert repair_profile_for_task(title, None) == "hermes-maintenance-steward"


def test_title_fence_context_carries_across_later_dash_clause():
    title = "GitHub Actions pin drift: ```text — Automation credential is missing ``` — inspect"
    assert repair_profile_for_task(title, None) == "dependency-tooling-health-sentinel"


def test_title_fence_opened_after_inspect_clause_carries_across_dashes():
    title = "GitHub Actions pin drift: inspect — ```text — Automation credential is missing ```"
    assert repair_profile_for_task(title, None) == "dependency-tooling-health-sentinel"


def test_removed_inventory_segments_cannot_join_to_synthesize_a_blocker():
    body = "Automation credential \nInventory:\nis\n\n missing"
    assert repair_profile_for_task(INTAKE_TITLES[0][0], body) == INTAKE_TITLES[0][1]


def test_title_and_body_fields_are_filtered_independently():
    inventory_title = "GitHub Actions pin drift: Inventory:\nAutomation credential is missing in example."
    direct_body_blocker = "Usage limits were exceeded."
    assert repair_profile_for_task(inventory_title, direct_body_blocker) == "hermes-maintenance-steward"

    direct_title_blocker = "GitHub Actions pin drift: Automation credential is missing."
    inventory_body = "Inventory:\nUsage limits were exceeded."
    assert repair_profile_for_task(direct_title_blocker, inventory_body) == "hermes-maintenance-steward"
