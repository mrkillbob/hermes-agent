"""A public capacity notice carries no approval or bytes from unavailable guidance."""
import logging


def unavailable_capacity_notice(path, target, raw_bytes, *, working_root, read_target):
    from agent.source_provenance import MAX_SOURCE_SLICE_BYTES, MAX_SOURCE_SLICE_LINES, get_read_block_error
    from agent.source_context_diagnostic import capacity_notice
    from agent.source_context_presentation import read_context_bytes
    from agent.subdirectory_hints import _resolved_hint_target

    lines = raw_bytes.count(b'\n') + int(not raw_bytes.endswith(b'\n'))
    reason = ('byte' if len(raw_bytes) > MAX_SOURCE_SLICE_BYTES
              else 'line' if lines > MAX_SOURCE_SLICE_LINES else None)
    if reason is None or read_target != target:
        return None
    # Slice capacity checks precede registry I/O. Independently retain canonical
    # policy and exact current-byte checks before replacing an unsupported hint.
    try:
        if get_read_block_error(str(target)) is not None:
            return None
        if read_context_bytes(target) != raw_bytes:
            return None
        if (_resolved_hint_target(path, working_root) != target
                or get_read_block_error(str(target)) is not None):
            return None
    except Exception:
        logging.getLogger(__name__).debug('Guidance capacity verification unavailable', exc_info=True)
        return None
    return capacity_notice(path.name, reason)


def protected_capacity_context(agent):
    """Mirror the actual owner's existing protected-context decision, not tool metadata."""
    import os
    from agent.llm_egress_runtime import _route_for_agent, _route_field, provider_uses_egress_firewall
    from agent.remote_task_context import owned_remote_task

    try:
        route = _route_for_agent(agent, None)
        return (provider_uses_egress_firewall(_route_field(route, 'provider', ''))
                or owned_remote_task(agent, route=route)
                or os.environ.get('HERMES_KANBAN_PROTECTED_REMOTE') == '1')
    except Exception:
        logging.getLogger(__name__).debug('Capacity presentation route unavailable', exc_info=True)
        return False
