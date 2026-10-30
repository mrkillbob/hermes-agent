"""Stateless wording for an interrupted tool call.

The old gateway can keep agent.interrupt_control cached while an update replaces
the checkout. New tool-executor code must not require a newly added attribute from
that cached module during the handoff.
"""

USER_INTERRUPT_REASONS = frozenset({
    "explicit stop requested",
    "user sent a new message",
    "user interrupt",
})


def interrupt_skip_wording(agent) -> str:
    """Describe the recorded interrupt reason without assigning a system abort to the user."""
    reason = getattr(agent, "_tool_interrupt_reason", None)
    if not reason:
        return "Turn interrupted"
    if reason in USER_INTERRUPT_REASONS:
        return reason.capitalize()
    return f"Turn aborted — {str(reason).replace('_', ' ')}"
