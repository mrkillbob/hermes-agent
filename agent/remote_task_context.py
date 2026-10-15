"""Route-derived context policy for a dispatcher-owned task, independent of provider lists."""
from collections.abc import Mapping
from typing import Any


def owned_remote_task(agent: Any, *, route: Any = None) -> bool:
    """Require remote handling unless an owned task's actual route is demonstrably local.

    Unknown destinations remain on the guarded path so the final firewall can reject
    them. Inherited task environment does not confer ownership on delegates or cron.
    """
    from agent.delegation_context import owned_kanban_task
    from agent.llm_egress_firewall import DestinationClass, classify_destination

    if not owned_kanban_task():
        return False
    actual = agent if route is None else route
    fields = (
        actual.get(name) if isinstance(actual, Mapping) else getattr(actual, name, None)
        for name in ('provider', 'base_url', 'api_mode')
    )
    destination = classify_destination(*fields)
    return destination not in {DestinationClass.LOCAL_PROCESS, DestinationClass.LOOPBACK}


def reject_remote_task_request(agent: Any, route: Any, reason: str) -> None:
    """Record a content-free rejection before an unsupported send can begin."""
    from hashlib import sha256
    from pathlib import Path

    from agent.llm_egress_firewall import EgressDecision, LLMEgressFirewall, classify_destination
    from agent.source_provenance import DEFAULT_POLICY_DIGEST
    from hermes_constants import get_hermes_home

    def field(name):
        return str((route.get(name) if isinstance(route, Mapping) else getattr(route, name, None)) or '')

    policy = getattr(agent, '_llm_egress_policy_digest', '') or DEFAULT_POLICY_DIGEST
    state_dir = getattr(agent, '_llm_egress_state_dir', None) or Path(get_hermes_home()) / 'egress'
    decision = EgressDecision(
        allowed=False,
        destination_class=classify_destination(field('provider'), field('base_url'), field('api_mode')),
        provider=field('provider'), model=field('model'), base_url=field('base_url'), api_mode=field('api_mode'),
        payload_sha256=sha256(b'').hexdigest(), serialized_bytes=0, estimated_tokens=0,
        source_grant_count=0, source_segment_count=0,
        session_id=str(getattr(agent, 'session_id', '') or ''),
        turn_id=str(getattr(agent, '_current_turn_id', '') or ''),
        request_id=str(getattr(agent, '_current_api_request_id', '') or ''),
        policy_digest=policy, reason_codes=(reason,),
    )
    LLMEgressFirewall(Path(state_dir), policy_digest=policy)._block(decision)


def guard_remote_task_transport(agent: Any, *, route: Any = None) -> None:
    """Native SDK/subprocess routing is not proven by the agent's base URL."""
    from agent.delegation_context import owned_kanban_task
    from agent.llm_egress_runtime import egress_enforcement_enabled

    if not owned_kanban_task() or not egress_enforcement_enabled():
        return
    actual = agent if route is None else route
    unresolved = {name: actual.get(name) if isinstance(actual, Mapping) else getattr(actual, name, None)
                  for name in ('provider', 'model', 'api_mode')}
    reject_remote_task_request(agent, unresolved, 'unsupported_remote_task_transport')


def require_public_task_context(agent: Any, *, route: Any) -> None:
    """A local or unverified restored prompt cannot become public through fallback."""
    if not owned_remote_task(agent, route=route):
        return
    if getattr(agent, '_remote_task_history_unverified', False) is True:
        reject_remote_task_request(agent, route, 'unverified_remote_task_history')
    mode = getattr(agent, '_remote_task_context_mode', None)
    cached = getattr(agent, '_cached_system_prompt', None)
    approved = getattr(agent, '_remote_task_public_prompt', None)
    if mode == 'local' or (cached is not None and (mode != 'public' or cached != approved)):
        reject_remote_task_request(agent, route, 'unverified_remote_task_context')


def task_ephemeral_system_prompt(agent: Any) -> str | None:
    """Managed tasks retain explicit launch skills, without ambient personality."""
    if owned_remote_task(agent):
        return getattr(agent, '_remote_task_ephemeral_system_prompt', None)
    return getattr(agent, 'ephemeral_system_prompt', None)


def compose_task_system_prompt(agent: Any, prompt: str | None) -> str:
    """Use the same task-scoped additions on the main, summary and fallback paths."""
    mark_local_task_context(agent)
    ephemeral = task_ephemeral_system_prompt(agent)
    return ((prompt or '') + '\n\n' + ephemeral).strip() if ephemeral else (prompt or '')


def task_prefill_messages(agent: Any) -> Any:
    """Profile examples are ambient context, never an owned remote task's input."""
    mark_local_task_context(agent)
    return () if owned_remote_task(agent) else (getattr(agent, 'prefill_messages', None) or ())


def mark_task_history_unverified(agent: Any, history: Any = True) -> None:
    """Keep history provenance sticky across route changes and SYSTEM rebuilds.

    Restored history and summaries have no per-message public provenance. Only
    constructing a fresh agent for a new conversation starts a clean boundary.
    """
    from agent.delegation_context import owned_kanban_task

    if history and owned_kanban_task():
        agent._remote_task_history_unverified = True


def mark_local_task_context(agent: Any, *, route: Any = None) -> None:
    """A local model or ambient producer may add private prose to retained history."""
    if not owned_remote_task(agent, route=route):
        mark_task_history_unverified(agent)
