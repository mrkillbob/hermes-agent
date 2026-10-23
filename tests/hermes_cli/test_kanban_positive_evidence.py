"""Only complete, affirmative records may override the intake owner."""

import pytest

from hermes_cli.kanban_repair_routing import repair_profile_for_task


INTAKE_TITLES = [
    ("Vite lock stale: inspect dependencies", "dependency-tooling-health-sentinel"),
    ("Regenerate Python lock — inspect dependencies", "dependency-tooling-health-sentinel"),
    ("Local tooling refresh – inspect dependencies", "dependency-tooling-health-sentinel"),
    ("GitHub Actions pin drift - inspect dependencies", "dependency-tooling-health-sentinel"),
    ("Diagnostic experiment: compare LunaBot replay ordering", "research-experiment-runner"),
]

POSITIVE_RECORDS = [
    "Automation credential is missing",
    "GH auth failure",
    "GH auth failure stopped the workflow",
    "Provider returned usage limits for this account",
    "Workflow quota is exhausted",
    "Quota failed",
    "Provider quota was exceeded",
    "Account usage limits were exceeded",
    "Automation usage limits have been exhausted",
    "Quota failures are blocking the workflow",
    "Quota failure blocked the workflow",
    "Quota blocked the workflow retry",
    "Workflow quota stopped the workflow",
    "Quota prevented the workflow from running",
    "Usage limits blocked the workflow",
    "Usage limits prevented the workflow from running",
    "Usage limits failed",
    "The provider hit usage limits, so the workflow cannot run",
    "The workflow encountered usage limits during the run",
    "Workflow has failed due to usage limits",
    "Provider has exceeded usage limits",
    "Provider has exhausted usage limits",
    "LunaBot diagnostics failed",
    "LunaBot diagnostics have failed",
    "LunaBot diagnostic is unavailable",
    "LunaBot diagnostics returned an error",
    "missing hermes_cli",
    "audit-pr unavailable",
    "inspect-pr unavailable",
    "audit_deferred",
    "untrusted receipt",
]

STATUS_FIELDS = ["status", "error", "failure", "report", "operational blocker", "Later report"]
ALLOWED_TAILS = [
    "for this account",
    "for the automation account",
    "during the run",
    "while collecting replay data",
    "before LunaBot replay",
    "before comparing LunaBot replay ordering",
    ", so the workflow cannot run",
    ", so the workflow can't run",
]

QUESTION_RECORDS = [
    "Were quota failures blocking the workflow?",
    "Did the provider hit usage limits?",
    "Have usage limits blocked the workflow?",
    "Have LunaBot diagnostics failed?",
    "Is audit-pr unavailable?",
    "Is the automation credential missing?",
    "Why did the workflow fail because automation credential is missing?",
    "Any evidence that provider quota is exhausted?",
    "Automation credential is missing?",
    "It is unclear whether the workflow failed because automation credential is missing",
    "Automation credential is missing, if confirmed",
    "The workflow report says “Automation credential is missing. LunaBot diagnostics failed.”",
]

UNSUPPORTED_RECORDS = [
    "Example: Automation credential is missing",
    "The card suggests that automation credential is missing",
    "We suspect the provider hit usage limits",
    "The provider might hit usage limits",
    "The provider did not hit usage limits",
    "The workflow failed, but not due to usage limits",
    "The provider remains within its usage limits",
    "Assess provider usage limits before updating action pins",
    "Usage limits documentation is attached for the dependency review",
    "The workflow failed due to usage limits for a different project",
    "The provider hit usage limits, so the workflow may be delayed",
    "The provider hit usage limits and more investigation is needed",
    "Workflow quota is exhausted; perhaps it will recover",
    "We cannot confirm that the workflow report says automation credential is missing",
    "The workflow report says ‘Automation credential is missing. LunaBot diagnostics failed.’",
    "Automation credential is missing; LunaBot diagnostics failed?",
    "The workflow failed?; automation credential is missing",
    "The - automation credential is missing",
    "Later, - automation credential is missing",
    "The the automation credential is missing",
]


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("record", POSITIVE_RECORDS)
def test_canonical_complete_positive_records_route_maintenance(title, owner, record):
    assert repair_profile_for_task(title, record) == "hermes-maintenance-steward"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("field", STATUS_FIELDS)
def test_whole_record_status_fields_route_maintenance(title, owner, field):
    assert repair_profile_for_task(title, f"{field}: Automation credential is missing") == "hermes-maintenance-steward"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("frame", [
    "The workflow report says {}",
    "An operator reported that {}",
    "There is evidence that {}",
    "We found an indication that {}",
])
def test_accepted_report_frames_route_maintenance(title, owner, frame):
    assert repair_profile_for_task(title, frame.format("Automation credential is missing")) == "hermes-maintenance-steward"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("record", [
    "The workflow report says ‘LunaBot diagnostics returned an error’.",
    "An operator reported that `LunaBot diagnostics returned an error`.",
    "The deployment failed because ‘LunaBot diagnostics returned an error’.",
    "The workflow failed because ``automation credential is missing``.",
])
def test_actual_quoted_positive_records_in_accepted_frames_route(title, owner, record):
    assert repair_profile_for_task(title, record) == "hermes-maintenance-steward"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("frame", [
    "The workflow failed because {}",
    "The deployment failed, because {}",
    "The workflow failed due to {}",
    "The deployment cannot run because of {}",
    "The workflow can't run because {}",
    "The deployment didn't run when {}",
])
def test_accepted_failure_lead_ins_route_maintenance(title, owner, frame):
    assert repair_profile_for_task(title, frame.format("automation credential is missing")) == "hermes-maintenance-steward"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("tail", ALLOWED_TAILS)
def test_finite_supported_tails_route_maintenance(title, owner, tail):
    separator = "" if tail.startswith(",") else " "
    assert repair_profile_for_task(
        title, f"Automation credential is missing{separator}{tail}",
    ) == "hermes-maintenance-steward"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("prefix", ["", "The ", "Current ", "Later, ", "- "])
def test_supported_direct_framing_routes_maintenance(title, owner, prefix):
    assert repair_profile_for_task(title, f"{prefix}Automation credential is missing") == "hermes-maintenance-steward"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("record", [
    "- status: Automation credential is missing",
    "* The workflow report says ‘LunaBot diagnostics failed’",
    "- Later, automation credential is missing",
])
def test_simple_list_bullets_frame_supported_records(title, owner, record):
    assert repair_profile_for_task(title, record) == "hermes-maintenance-steward"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("record", QUESTION_RECORDS + UNSUPPORTED_RECORDS)
def test_questions_and_unsupported_records_keep_title_owner(title, owner, record):
    assert repair_profile_for_task(title, record) == owner


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("record", [
    "Documentation says:\nAutomation credential is missing",
    "Check whether:\nAutomation credential is missing",
    "notes:\n\nAutomation credential is missing",
    "Documentation contains: Automation credential is missing; LunaBot diagnostics failed",
    "Documentation contains “The workflow failed. Automation credential is missing.”",
    "The report quotes ‘LunaBot diagnostics failed. Automation credential is missing.’",
    "The workflow failed because “automation credential is missing — perhaps”.",
    "Example: `automation credential is missing` Current LunaBot diagnostics have failed",
])
def test_unknown_records_and_opaque_quotes_keep_title_owner(title, owner, record):
    assert repair_profile_for_task(title, record) == owner


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("record", [
    "The workflow report says\nAutomation credential is missing.",
    "Status:\nAutomation credential is missing.",
    "An operator reported that ‘LunaBot diagnostics failed’. ",
    "The workflow failed because `automation credential is missing`.",
    "Automation credential is missing; LunaBot diagnostics failed",
    "Automation credential is missing; why?",
    "Automation credential is missing; retry is blocked",
])
def test_soft_lines_and_accepted_compounds_route_maintenance(title, owner, record):
    assert repair_profile_for_task(title, record) == "hermes-maintenance-steward"


@pytest.mark.parametrize("record", [
    'The workflow report says "Automation credential is missing."',
    'The workflow report says “Automation credential is missing.”',
    "An operator reported that ‘LunaBot diagnostics failed.’",
    'The workflow report says "Automation credential is missing!"',
    'The workflow failed because "Automation credential is missing."',
    "The deployment cannot run because ‘LunaBot diagnostics failed!’",
])
@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
def test_report_quotes_allow_terminal_sentence_punctuation_inside_quote(title, owner, record):
    assert repair_profile_for_task(title, record) == "hermes-maintenance-steward"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("record", [
    "Automation credential is missing; perhaps the workflow will recover",
    "Automation credential is missing; was the workflow blocked?",
    "Documentation contains: Automation credential is missing; LunaBot diagnostics failed",
    "Compare the phrase `LunaBot diagnostics failed`; current LunaBot diagnostics have failed",
    "There is no evidence that Automation credential is missing",
    "We found no indication that LunaBot diagnostics failed",
    "The workflow failed because maybe automation credential is missing",
])
def test_unknown_compounds_prefixes_and_negations_keep_title_owner(title, owner, record):
    assert repair_profile_for_task(title, record) == owner


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
def test_period_separated_positive_after_unsupported_record_routes(title, owner):
    body = "Compare the phrase `LunaBot diagnostics failed`. Current LunaBot diagnostics have failed."
    assert repair_profile_for_task(title, body) == "hermes-maintenance-steward"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
def test_later_independent_positive_survives_unknown_record(title, owner):
    body = "Documentation says: automation credential is missing.\nCurrent LunaBot diagnostics have failed."
    assert repair_profile_for_task(title, body) == "hermes-maintenance-steward"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
@pytest.mark.parametrize("body", [
    "Assessment concerns health.\nAutomation credential is missing.",
    "```text\nexample card\n```\nAutomation credential is missing.",
    "Inventory:\n\n```text\nsample\n```\n\nAutomation credential is missing.",
    "Inventory:\n\n~~~text\nsample\n~~~\n\nAutomation credential is missing.",
])
def test_independent_positive_survives_soft_newline_or_closed_removed_block(title, owner, body):
    assert repair_profile_for_task(title, body) == "hermes-maintenance-steward"


@pytest.mark.parametrize("title", [
    "Vite lock stale: Provider quota was exceeded",
    "Regenerate Python lock — Provider quota was exceeded",
    "Local tooling refresh – Provider quota was exceeded",
    "GitHub Actions pin drift - Provider quota was exceeded",
    "Diagnostic experiment: compare LunaBot replay — LunaBot diagnostics failed",
])
def test_title_positive_evidence_after_supported_family_frame_routes(title):
    assert repair_profile_for_task(title, "Documentation is attached.") == "hermes-maintenance-steward"


@pytest.mark.parametrize("title", [
    "Diagnostic experiment: LunaBot diagnostics failed before LunaBot replay",
    "Diagnostic experiment: compare LunaBot replay ordering. LunaBot diagnostics failed.",
])
def test_diagnostic_title_accepts_defined_tail_or_independent_sentence(title):
    assert repair_profile_for_task(title, None) == "hermes-maintenance-steward"


def test_unknown_diagnostic_title_dash_does_not_create_assertion():
    title = "Diagnostic experiment: compare LunaBot replay — inspect — LunaBot diagnostics failed"
    assert repair_profile_for_task(title, None) == "research-experiment-runner"


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
def test_inventory_and_quote_barriers_do_not_promote_embedded_records(title, owner):
    body = (
        "Quoted: Automation credential is\n"
        "missing.\n\n"
        "```text\nLunaBot diagnostics failed.\n```\n"
        "Inventory:\nProvider quota was exceeded."
    )
    assert repair_profile_for_task(title, body) == owner


@pytest.mark.parametrize(("title", "owner"), INTAKE_TITLES)
def test_later_positive_after_a_removed_context_still_routes(title, owner):
    body = "Quoted inventory:\nAutomation credential is missing.\n\nCurrent LunaBot diagnostics have failed."
    assert repair_profile_for_task(title, body) == "hermes-maintenance-steward"
