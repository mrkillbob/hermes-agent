"""Validate the dispatcher's explicit local route before owner claim reads.

Loopback is an endpoint constraint, not a no-paid or model-readiness receipt.
"""
from urllib.parse import urlsplit

from hermes_cli.kanban_worker_routing import _LOCAL_KANBAN_PROVIDERS
from hermes_state_runtime import RuntimeStoreError


def validate_local_worker_route(route):
    """Refuse unresolved/remote routes without rewriting model or reasoning."""
    if not isinstance(route, dict):
        raise RuntimeStoreError('invalid_params')
    if not route:
        return
    provider, model, endpoint = (route.get(k) for k in ('provider', 'model', 'base_url'))
    if (not isinstance(provider, str) or provider not in _LOCAL_KANBAN_PROVIDERS or not isinstance(model, str)
            or not model.strip() or not isinstance(endpoint, str)
            or any(ord(char) <= 32 for char in endpoint)):
        raise RuntimeStoreError('invalid_params')
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
        valid = (parsed.scheme in {'http', 'https'}
                 and parsed.hostname in {'localhost', '127.0.0.1', '::1'}
                 and parsed.username is None and parsed.password is None
                 and (port is None or 0 < port <= 65535))
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeStoreError('invalid_params')
