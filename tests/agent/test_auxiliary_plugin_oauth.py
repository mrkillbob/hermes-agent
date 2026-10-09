"""Plugin OAuth registry routes keep the session bearer and provider-owned transport."""

from types import SimpleNamespace

import pytest

from agent import auxiliary_client as aux
from providers.base import ProviderProfile


@pytest.mark.parametrize("auth_type", ["oauth_external", "oauth_device_code"])
@pytest.mark.parametrize("explicit", [False, True])
def test_registry_oauth_route_uses_profile_client_and_own_credential(monkeypatch, auth_type, explicit):
    import hermes_cli.auth as auth
    import providers

    calls = []
    client = object()

    class Profile(ProviderProfile):
        def create_client(self, **kwargs):
            calls.append(kwargs)
            return client

    name = "fixture-oauth"
    profile = Profile(name=name, auth_type=auth_type, base_url="https://fixture.invalid/v1")
    monkeypatch.setitem(auth.PROVIDER_REGISTRY, name, SimpleNamespace(auth_type=auth_type))
    monkeypatch.setattr(providers, "get_provider_profile", lambda provider: profile if provider == name else None)
    pool_calls = []

    def select(provider):
        pool_calls.append(provider)
        return True, SimpleNamespace(runtime_api_key="pool-token", runtime_base_url="https://pool.invalid/v1")

    monkeypatch.setattr(aux, "_select_pool_entry", select)
    # A callable bearer must remain deferred; evaluating it here would freeze an expiring token.
    def bearer():
        pytest.fail("the resolver must not invoke the bearer")

    req = SimpleNamespace(provider=name, model="fixture-model", explicit_api_key=bearer if explicit else "",
                          explicit_base_url="https://session.invalid/v1" if explicit else "", async_mode=False)

    result = aux._resolve_registry_branch(req)

    assert result == (client, "fixture-model")
    assert calls == [{"api_key": bearer if explicit else "pool-token",
                      "base_url": "https://session.invalid/v1" if explicit else "https://pool.invalid/v1"}]
    assert pool_calls == ([] if explicit else [name])


def test_failed_oauth_profile_hook_leaves_the_route_unavailable(monkeypatch, caplog):
    import hermes_cli.auth as auth
    import providers

    class Profile(ProviderProfile):
        def create_client(self, **kwargs):
            raise RuntimeError("fixture transport unavailable")

    name = "fixture-failed-oauth"
    profile = Profile(name=name, auth_type="oauth_external", base_url="https://fixture.invalid/v1")
    monkeypatch.setitem(auth.PROVIDER_REGISTRY, name, SimpleNamespace(auth_type="oauth_external"))
    monkeypatch.setattr(providers, "get_provider_profile", lambda provider: profile if provider == name else None)
    req = SimpleNamespace(provider=name, model="fixture-model", explicit_api_key=" session-token ",
                          explicit_base_url="https://session.invalid/v1", async_mode=False)

    assert aux._resolve_registry_branch(req) == (None, None)
    assert "client hook failed" in caplog.text
