"""Trusted in-process retained diagnostic seal; no host or session authorizer.

This is the minimal retained-state contract of the pending diagnostic bridge.
Only trusted application code may bind it; no request, note, model field or
configuration enables it. There is no production issuer or delivery registration.
Other bridge protections and live adoption remain separately unqualified.
"""
from dataclasses import dataclass


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
    agent._diagnostic_reference_policy = policy
