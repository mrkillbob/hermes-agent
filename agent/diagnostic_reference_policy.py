"""Trusted in-process retained diagnostic seal; no host or session authorizer.

This is the minimal retained-state contract of the pending diagnostic bridge.
Only trusted application code may bind it; no request, note, model field or
configuration enables it. There is no production issuer or delivery registration.
Other bridge protections and live adoption remain separately unqualified.
"""
from dataclasses import dataclass
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from functools import wraps
from threading import Lock

_output_scope = ContextVar('diagnostic_reference_output', default=None)


def private_output_active():
    return _output_scope.get() is not None


@contextmanager
def _private_output_scope(agent, policy):
    from agent.local_text_policy import LocalTextRestriction, local_text_scope
    if retained_policy(agent) is not policy:
        raise LocalTextRestriction('diagnostic_policy_mismatch')
    token = _output_scope.set((agent, policy))
    try:
        with local_text_scope(True):
            yield
    finally:
        _output_scope.reset(token)


@dataclass(frozen=True)
class DiagnosticPolicy:
    host_request: str
    acknowledgment_json: str
    session_id: str


def retained_policy(agent):
    value = getattr(agent, '_diagnostic_reference_policy', None)
    return value if type(value) is DiagnosticPolicy else None


def bind_agent(agent, policy):
    if type(policy) is not DiagnosticPolicy:
        raise TypeError('invalid_diagnostic_policy')
    existing = retained_policy(agent)
    if (getattr(agent, 'session_id', None) != policy.session_id
            or (existing is not None and existing is not policy)):
        raise RuntimeError('diagnostic_session_mismatch')
    if existing is None:
        agent._diagnostic_run_gate = Lock()
    agent._diagnostic_reference_policy = policy


def lifecycle_export_messages(agent, messages):
    """A retained seal outlives turn scope; extraction must not archive its context.

    No transcript selection or acknowledgment is inferred. Keep cleanup hooks
    running with empty context, including after a session-id change. Ordinary
    sessions keep the original object and their existing extraction behavior.
    """
    return [] if retained_policy(agent) is not None else messages


def require_agent_tools_allowed(agent):
    """Retained refusal, independent of thread context or current session label.

    This only restricts an already sealed agent; it supplies no host permission,
    note selection, executor registration or evidence of reference consumption.
    """
    from agent.local_text_policy import LocalTextRestriction, require_tools_allowed
    if retained_policy(agent) is not None:
        raise LocalTextRestriction('diagnostic_reference_tools_denied')
    require_tools_allowed()


def project_result(policy, result):
    """Only structural façade status may leave a sealed run; this is not a receipt.

    No model response, transcript, error text, arbitrary counters or policy fields
    are copied. A completed agent call alone cannot establish consumption or ACK.
    Ordinary callers retain their exact original result object.
    """
    if type(policy) is not DiagnosticPolicy:
        return result
    valid = type(result) is dict
    return {'final_response': '', 'messages': [], 'completed': False,
            'failed': not valid or result.get('failed') is True or result.get('error') is not None,
            'interrupted': valid and result.get('interrupted') is True,
            'diagnostic_reference_execution': {'facade_returned': True,
                'provider_receipt': False, 'session_acknowledged': False}}


def sealed_output_boundary(run):
    """Include finally/cleanup errors; ordinary agents keep exact return semantics.

    A failed join leaves the seal and owned context intact. Unknown cleanup is not
    disposal, and async waiter cancellation cannot manufacture physical completion.
    """
    @wraps(run)
    def guarded(agent, *args, **kwargs):
        policy = retained_policy(agent)
        ambient = _output_scope.get()
        if ambient is not None and ambient[0] is not agent:
            from agent.local_text_policy import LocalTextRestriction
            raise LocalTextRestriction('diagnostic_cross_agent_unavailable')
        if policy is None:
            return run(agent, *args, **kwargs)
        gate = getattr(agent, '_diagnostic_run_gate', None)
        if (getattr(agent, '_diagnostic_cleanup_unknown', False)
                or gate is None or not gate.acquire(blocking=False)):
            result = project_result(policy, {'failed': True})
            result['diagnostic_reference_execution']['cleanup_complete'] = False
            return result
        agent._diagnostic_cleanup_done = False
        try:
            try:
                with _private_output_scope(agent, policy):
                    result = run(agent, *args, **kwargs)
            except BaseException as exc:
                if exception_surface(policy, exc) is exc:
                    raise
                result = {'failed': True}
            result = project_result(policy, result)
            result['diagnostic_reference_execution']['cleanup_complete'] = agent._diagnostic_cleanup_done
            return result
        finally:
            if not agent._diagnostic_cleanup_done:
                agent._diagnostic_cleanup_unknown = True
            gate.release()
    return guarded


def private_relay_unmanaged(session_id):
    """A limiter, not permission: never bypass a required managed Relay pipeline.

    Lookup only existing hosts, before resolution can restore a cold session or
    copy any request into native interceptors/exporters. No native private-content
    policy has been qualified; required/unknown managed execution is unavailable.
    """
    if not private_output_active():
        return False
    from agent import relay_runtime
    from agent.local_text_policy import LocalTextRestriction
    try:
        turn = relay_runtime.active_turn(session_id)
        hosts = [turn.lease.host if turn is not None else None,
                 relay_runtime.get_runtime(create=False)]
        if any(host is not None and host.managed_execution_enabled() is not False for host in hosts):
            raise LocalTextRestriction('diagnostic_managed_relay_unavailable')
    except Exception:
        raise LocalTextRestriction('diagnostic_managed_relay_unavailable') from None
    return True


def exception_surface(policy, error):
    """Withhold ordinary error payloads; keep control-flow exceptions unchanged."""
    if (type(policy) is not DiagnosticPolicy or not isinstance(error, Exception)
            or isinstance(error, InterruptedError) or type(error).__name__ == 'CancelledError'):
        return error
    return RuntimeError('diagnostic_reference_execution_failed')


def preview_for_log(agent, message, summarize):
    return '[diagnostic reference withheld]' if retained_policy(agent) is not None else summarize(message)


def dump_request_debug(agent, api_kwargs, *, reason, error=None):
    """Guard before copying a request, reading credentials or creating a file."""
    if retained_policy(agent) is not None:
        return None
    from agent.agent_runtime_helpers import dump_api_request_debug
    return dump_api_request_debug(agent, api_kwargs, reason=reason, error=error)


def release_turn_context(agent, policy):
    """After synchronous turn cleanup/join, drop only this agent's owned references.

    Never mutate caller history, unseal, close the shared session's resources or
    claim SDK-thread memory zeroization. An async waiter is not physical completion.
    """
    if type(policy) is not DiagnosticPolicy:
        return
    messages = getattr(agent, '_session_messages', None)
    if isinstance(messages, list):
        with suppress(Exception):
            from agent.model_metadata_token_memo import forget_message_token_memo
            forget_message_token_memo(messages,
                keep=(getattr(agent, '_cached_system_prompt', None),
                      getattr(agent, '_cached_system_prompt_static', None)))
    agent._session_messages = []
    agent._db_flush_scan_prefix = None
    agent._streamed_assistant_text_parts = []
    agent._llm_output_transform = None
    agent._reused_response_text = None
    agent._pending_cli_user_message = None
    agent._diagnostic_cleanup_done = True
