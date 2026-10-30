"""Restriction scope is local to an admission and cannot grant auxiliary work."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest


def test_scoped_denial_is_isolated_and_resets_on_exception():
    from agent.local_text_policy import LocalTextRestriction, local_text_only, local_text_scope, require_auxiliary_allowed
    assert not local_text_only()
    with pytest.raises(LookupError):
        with local_text_scope(True):
            with pytest.raises(LocalTextRestriction): require_auxiliary_allowed()
            with ThreadPoolExecutor(1) as executor:
                assert executor.submit(local_text_only).result() is False
            raise LookupError('fixture')
    assert not local_text_only()


@pytest.mark.asyncio
async def test_auxiliary_denies_before_relay_queue_and_client_resolution(monkeypatch):
    from agent import auxiliary_client as aux
    from agent.local_text_policy import LocalTextRestriction, local_text_scope
    def forbidden(*args, **kwargs): pytest.fail('auxiliary resolution must not start')
    monkeypatch.setattr(aux, '_acquire_sync_aux_semaphore', forbidden)
    monkeypatch.setattr(aux, '_acquire_async_aux_semaphore', forbidden)
    monkeypatch.setattr(aux, '_resolve_task_provider_model', forbidden)
    monkeypatch.setattr(aux, '_validate_proxy_env_urls', forbidden)
    entered, release = asyncio.Event(), asyncio.Event()
    async def restricted():
        with local_text_scope(True):
            entered.set()
            await release.wait()
            for fn in [lambda: aux.call_llm(messages=[]), lambda: aux.get_text_auxiliary_client(),
                       lambda: aux.resolve_provider_client('nvidia'),
                       lambda: aux._create_openai_client(api_key='fixture', base_url='https://remote.invalid'),
                       lambda: aux._relay_sync_completion(None, {}), lambda: aux._relay_sync_stream(None, {})]:
                with pytest.raises(LocalTextRestriction): fn()
            with pytest.raises(LocalTextRestriction): await aux.async_call_llm(messages=[])
            with pytest.raises(LocalTextRestriction): await aux._relay_async_completion(None, {})
    async def ordinary():
        from agent.local_text_policy import local_text_only
        await entered.wait()
        assert not local_text_only()
        release.set()
    await asyncio.gather(restricted(), ordinary())
