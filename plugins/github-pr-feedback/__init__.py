"""Host-owned registration; intake mode imports no broad controllers or hooks."""

import sys
from importlib import import_module


def _module(name):
    if __package__ and __package__ in sys.modules:
        return import_module(f".github_pr_feedback.{name}", __package__)
    return import_module(f"github_pr_feedback.{name}")


def register(ctx):
    getter = getattr(ctx, "get_config", lambda key, default=None: default)
    absent = object()
    intake = getter("intake_only", default=absent)
    # Explicit new-mode presence is sticky even when its flag is disabled or
    # malformed. Legacy absence preserves protective completion guards.
    if intake is not absent:
        setup, handler = _module("intake_cli").cli_bindings(ctx)
    else:
        _module("completion_guard").register_completion_guard(ctx)
        _module("repair_completion_policy").register_repair_completion_policy(ctx)
        setup, handler = _module("cli").cli_bindings(ctx)
    ctx.register_cli_command(
        name="github-pr-feedback",
        help="Scan governed GitHub pull-request feedback",
        setup_fn=setup,
        handler_fn=handler,
        description="Separately guarded PR feedback intake and maintenance policies.",
    )
