"""Hermes directory-plugin entry point."""

try:
    from .github_pr_feedback.cli import cli_bindings
    from .github_pr_feedback.repair_completion_policy import register_repair_completion_policy
    from .github_pr_feedback.completion_guard import register_completion_guard
    from .github_pr_feedback.egress_projection import register as register_egress_projection
except ImportError:  # Direct module loading in lightweight test hosts.
    from github_pr_feedback.cli import cli_bindings
    from github_pr_feedback.repair_completion_policy import register_repair_completion_policy
    from github_pr_feedback.completion_guard import register_completion_guard
    from github_pr_feedback.egress_projection import register as register_egress_projection


def register(ctx) -> None:
    """Register the standalone, host-owned CLI command tree."""

    register_egress_projection()
    register_completion_guard(ctx)
    register_repair_completion_policy(ctx)
    setup_fn, handler_fn = cli_bindings(ctx)
    ctx.register_cli_command(
        name="github-pr-feedback",
        help="Scan governed GitHub pull-request feedback",
        setup_fn=setup_fn,
        handler_fn=handler_fn,
        description="Read-only GitHub feedback intake with blocked Kanban cards.",
    )
