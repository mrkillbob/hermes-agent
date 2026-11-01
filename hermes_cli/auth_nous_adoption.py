"""Prepare already-valid Nous credentials from the bound profile without repair or refresh.

This is a candidate, not a credential lease or authorization to dispatch. Startup
recovery must check its generation again under the runtime lease before installation.
No authentication mutation or root/sibling fallback belongs in this interface.
"""
from dataclasses import dataclass, field
import hashlib
import json
import os
import stat
from pathlib import Path
import time

from hermes_cli.auth_constants import DEFAULT_NOUS_INFERENCE_URL


@dataclass(frozen=True)
class NousAdoption:
    profile_home: str
    model: str
    api_key: str = field(repr=False)
    generation: str = field(repr=False)
    pool_row_json: str = field(default='', repr=False)
    base_url: str = DEFAULT_NOUS_INFERENCE_URL


def _eligible_pool(store, state, model):
    pools = store.get('credential_pool', {})
    if not isinstance(pools, dict):
        return False
    entries = pools.get('nous', [])
    if entries == []:
        return True  # Singleton-only ownership; do not seed a pool merely to adopt.
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        return False  # Selection policy for multiple credentials stays with its existing owner.
    entry = entries[0]
    if (entry.get('source') != 'device_code' or entry.get('last_status') not in (None, 'ok')
            or entry.get('access_token') != state.get('access_token')
            or entry.get('agent_key') != state.get('agent_key')):
        return False
    if any(entry.get(key) != state.get(key) for key in ('scope', 'expires_at', 'agent_key_expires_at')):
        return False
    cooldowns = entry.get('model_cooldowns', {}) or {}
    if not isinstance(cooldowns, dict):
        return False
    until = cooldowns.get(model)
    return until is None or (type(until) in (int, float) and until <= time.time())


def prepare_nous_adoption(*, profile_home: Path, model: str):
    """Return a current-profile candidate, or None without any recovery side effects.

    Only canonical paid Nous chat routes with an unambiguous available singleton
    qualify. Refusing unsupported state preserves the fallback; it never repairs auth.
    """
    from hermes_constants import get_hermes_home, hermes_home_key
    from hermes_cli.auth import _auth_file_path, _nous_invoke_jwt_is_usable
    from hermes_cli.providers import nous_api_mode
    from hermes_cli.anon_auth import is_guest_state, is_anonymous_request

    owner = hermes_home_key(profile_home)
    if owner != hermes_home_key(get_hermes_home()) or not model or nous_api_mode(model) != 'chat_completions':
        return None
    path = _auth_file_path()
    try:
        if path.is_symlink() or hermes_home_key(path.parent) != owner:
            return None
        # Refuse platforms without no-follow open instead of racing a symlink check.
        nofollow = getattr(os, 'O_NOFOLLOW', None)
        if nofollow is None:
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | nofollow)
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                return None
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return None
        store = json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(store, dict) or not isinstance(store.get('providers'), dict):
        return None
    state = store['providers'].get('nous')
    if (not isinstance(state, dict) or is_guest_state(state) or state.get('anon_token')
            or not _eligible_pool(store, state, model)):
        return None
    base = state.get('inference_base_url') or DEFAULT_NOUS_INFERENCE_URL
    if not isinstance(base, str) or base.rstrip('/') != DEFAULT_NOUS_INFERENCE_URL.rstrip('/'):
        return None
    for token_key, expiry_key in (('agent_key', 'agent_key_expires_at'), ('access_token', 'expires_at')):
        token = state.get(token_key)
        if isinstance(token, str) and not is_anonymous_request('nous', token) and _nous_invoke_jwt_is_usable(
                token, scope=state.get('scope'), expires_at=state.get(expiry_key)):
            rows = store.get('credential_pool', {}).get('nous', [])
            row_json = json.dumps(rows[0], sort_keys=True) if rows else ''
            return NousAdoption(owner, model, token, hashlib.sha256(raw).hexdigest(), row_json)
    return None


def adoption_is_current(candidate: NousAdoption) -> bool:
    """Revalidate ownership, eligibility, expiry and store generation; never refresh."""
    current = prepare_nous_adoption(profile_home=Path(candidate.profile_home), model=candidate.model)
    return current is not None and current == candidate


def commit_current_adoption(candidate, publish):
    """Fence a prepared dictionary publication against cooperating auth writers.

    The caller holds its runtime reservation, not its bookkeeping mutex. Publication
    must neither acquire a pool lock nor run persistence/client callbacks.
    """
    from hermes_cli.auth import _auth_store_lock
    from hermes_constants import get_hermes_home, hermes_home_key
    if hermes_home_key(get_hermes_home()) != candidate.profile_home:
        return False
    with _auth_store_lock(timeout_seconds=0.0):
        if not adoption_is_current(candidate):
            return False
        publish()
        return True
