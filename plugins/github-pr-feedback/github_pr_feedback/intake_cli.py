"""Registered intake frontdoor; no maintenance CLI/controllers/hooks imported."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import partial

from .automation_client import automation_client
from .intake_context import registered_control_root, require_context
from .intake_kanban import IntakeKanban
from .intake_only import ASSIGNEE, SCOPES, IntakeError, IntakePolicy, scan_intake
from .policy import load_policy


def load_intake_policy(ctx, repository):
    section = ctx.get_config("intake_only", default={})
    if (
        not isinstance(section, dict)
        or set(section) != {"enabled"}
        or section["enabled"] is not True
        or ctx.get_config("enabled", default=False) is not False
        or ctx.get_config("auto_dispatch", default=False) is not False
        or repository not in SCOPES
    ):
        raise IntakeError("independent intake capability unavailable")
    # Reuse existing pure scope/reviewer/time validation. This enabled field
    # belongs only to the admission value; it never changes broad host settings.
    raw = {
        "enabled": True,
        "auto_dispatch": False,
        "assignee": ASSIGNEE,
        "board": SCOPES[repository],
    }
    for key in (
        "repositories",
        "reviewer_logins",
        "reviewer_associations",
        "include_self_feedback",
        "include_bot_feedback",
        "not_before",
        "github_identity",
    ):
        missing = object()
        value = ctx.get_config(key, default=missing)
        if value is not missing:
            raw[key] = value
    admission = load_policy(raw)
    return IntakePolicy(True, False, repository, admission)


def setup_cli(ctx, parser):
    commands = parser.add_subparsers(dest="github_pr_feedback_action", required=True)
    command = commands.add_parser(
        "scan-intake", help="Read scoped feedback and deliver deduplicated triage cards"
    )
    command.add_argument("--repository", required=True, choices=tuple(SCOPES))


def handle_cli(ctx, args):
    forbidden = (
        "assignee",
        "board",
        "model",
        "model_override",
        "provider",
        "provider_override",
        "base_url",
        "api_key",
        "reasoning_effort",
        "repair",
        "merge",
        "release",
        "labels",
        "dispatch",
        "push",
        "comment",
        "submit_review",
    )
    if getattr(args, "github_pr_feedback_action", None) != "scan-intake" or any(
        getattr(args, key, None) not in (None, False, "") for key in forbidden
    ):
        print(json.dumps({"status": "rejected", "created": 0}))
        return 1
    try:
        control_root = registered_control_root(ctx, SCOPES[args.repository])
        policy = load_intake_policy(ctx, args.repository)
        now = datetime.now(UTC)
        policy.check(now)  # time/capability gates before credential/client access
        require_context(ctx, control_root, SCOPES[args.repository])
        github = automation_client(policy.admission, allow_actions=False)
        ledger = control_root / "github-pr-feedback" / "intake-only" / "receipts.sqlite3"
        result = scan_intake(
            policy,
            github,
            IntakeKanban(expected_root=control_root, context=ctx),
            ledger,
            now=now,
            legacy_path=control_root / "github-pr-feedback" / "ledger.sqlite3",
        )
    except Exception:  # health: allow BLE001 -- CLI boundary rejects unknown host/plugin errors without disclosing credential/configuration data.
        result = {"status": "rejected", "created": 0}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


def cli_bindings(ctx):
    from .caller_repository import bind_cli
    return partial(setup_cli, ctx), bind_cli(ctx, handle_cli)
