"""Integrate a provider-owned candidate without calling a refreshing resolver."""
from contextlib import suppress
from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class NousPrimaryIntent:
    profile_home: str
    model: str


def _eligible_nous_route(model):
    from hermes_cli.providers import get_provider, nous_api_mode
    from hermes_cli.auth_constants import DEFAULT_NOUS_INFERENCE_URL
    from agent.custom_entry_pool import named_custom_entry
    from agent.startup_primary_recovery import _has_resolution_hook
    from agent.secret_scope import get_secret_str
    definition = get_provider('nous', allow_network=False)
    return (definition is not None and definition.source == 'hermes'
            and definition.auth_type == 'oauth_device_code' and definition.transport == 'openai_chat'
            and definition.base_url.rstrip('/') == DEFAULT_NOUS_INFERENCE_URL.rstrip('/')
            and named_custom_entry('nous') is None and not _has_resolution_hook('nous')
            and not get_secret_str('NOUS_INFERENCE_BASE_URL', '')
            and nous_api_mode(model) == 'chat_completions')


def pending_nous_intent(agent):
    from hermes_constants import get_hermes_home, hermes_home_key
    try:
        if _eligible_nous_route(agent.model):
            return NousPrimaryIntent(hermes_home_key(get_hermes_home()), agent.model)
    except Exception:  # health: allow BLE001 -- Optional metadata must not abort fallback; exception text can contain profile secrets.
        return None
    return None


def _candidate_pool(candidate):
    """Hydrate one unpublished row; selection, refresh and persistence are forbidden."""
    from agent.credential_pool import CredentialPool, PooledCredential
    from hermes_cli.auth import _token_pairs_by_id
    if not candidate.pool_row_json:
        return None  # Singleton-only adoption has no qualified accounting contract yet.
    row = json.loads(candidate.pool_row_json)
    if not isinstance(row.get('id'), str) or not row['id'] or row.get('auth_type') != 'oauth':
        return None
    entry = PooledCredential.from_dict('nous', row)
    if (entry.runtime_api_key != candidate.api_key
            or str(entry.runtime_base_url or '').rstrip('/') != candidate.base_url.rstrip('/')):
        return None
    pool = CredentialPool('nous', [entry])
    pool._current_id = entry.id
    pool._persisted_token_pairs = _token_pairs_by_id([row])
    return pool


def recover_nous_adoption(agent, lease, *, profile_home, model):
    """Publish a prepared Nous runtime under the existing runtime/auth ownership fences."""
    from agent.local_text_policy import local_text_only, primary_client_scope
    from agent.nous_rate_guard import nous_rate_limit_remaining
    from hermes_cli.auth_nous_adoption import prepare_nous_adoption, commit_current_adoption
    from agent.startup_primary_recovery import PrimaryIntent, _prepare_primary
    from agent.auxiliary_client import _create_openai_client
    if lease.agent is not agent:
        raise ValueError('Nous adoption requires the owning runtime lease')
    with lease.controller.mutex:
        lease._check_owner_locked()
    client = None
    installed = False
    try:
        if (not _eligible_nous_route(model) or local_text_only()
                or (nous_rate_limit_remaining(read_only=True) or 0) > 0):
            return False
        candidate = prepare_nous_adoption(profile_home=Path(profile_home), model=model)
        if candidate is None:
            return False
        pool = _candidate_pool(candidate)
        if pool is None:
            return False
        with primary_client_scope():
            client = _create_openai_client(api_key=candidate.api_key, base_url=candidate.base_url)
            intent = PrimaryIntent('nous', model, candidate.base_url, 'chat_completions')
            prepared = _prepare_primary(agent, intent, client, credential_pool=pool)
        old_client = agent.client

        def publish():
            with lease.controller.mutex:
                lease._check_owner_locked()
            prepared.install(agent, flush=False)

        installed = commit_current_adoption(candidate, publish)
        if not installed:
            return False
        prepared.writes.flush()
    except Exception:
        if installed:
            raise
        return False
    finally:
        if client is not None and not installed:
            with suppress(Exception):
                client.close()
    if old_client is not None and old_client is not agent.client:
        with suppress(Exception):
            agent._retire_shared_openai_client(old_client, reason='startup_nous_adopted')
    with suppress(Exception):
        agent._emit_pending_fallback_notice()
        agent._clear_status_buffer()
        agent._emit_diagnostic_status(f'Primary model restored: {model} via nous.')
    return True
