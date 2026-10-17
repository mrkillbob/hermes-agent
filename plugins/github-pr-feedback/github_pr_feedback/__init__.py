"""Hermes GitHub feedback package; importing pure helpers has no hooks."""


def register(ctx):
    getter = getattr(ctx, "get_config", lambda key, default=None: default)
    absent = object()
    if getter("intake_only", default=absent) is not absent:
        return
    from .completion_guard import register_completion_guard
    from .repair_completion_policy import register_repair_completion_policy

    register_completion_guard(ctx)
    register_repair_completion_policy(ctx)
