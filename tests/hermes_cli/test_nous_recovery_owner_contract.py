"""Qualify source ownership and rotated-grant durability before startup recovery integration."""
import json
from pathlib import Path
import socket

import httpx
import pytest


@pytest.mark.parametrize('profile_owns_state', [True, False], ids=['profile-source', 'existing-root-fallback'])
@pytest.mark.parametrize('usable_refresh', [True, False], ids=['usable-token', 'invalid-inference-token'])
def test_rotated_grant_stays_with_credential_owner_after_validation(
    tmp_path, monkeypatch, profile_owns_state, usable_refresh,
):
    from hermes_cli import auth, auth_nous
    from tests.hermes_cli.test_auth_nous_provider import _invoke_jwt

    profile = tmp_path / 'profile'
    root = tmp_path / 'root'
    profile.mkdir()
    root.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(profile))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(auth, '_auth_file_path', lambda: profile / 'auth.json')
    monkeypatch.setattr(auth, '_global_auth_file_path', lambda: root / 'auth.json')

    def deny_network(*args, **kwargs):
        pytest.fail('synthetic qualification must never open a network connection')

    monkeypatch.setattr(socket, 'create_connection', deny_network)
    monkeypatch.setattr(socket.socket, 'connect', deny_network)
    state = dict(access_token=_invoke_jwt(), refresh_token='synthetic-old-grant',
                 scope='inference:invoke', portal_base_url='https://portal.invalid',
                 inference_base_url='https://inference-api.nousresearch.com/v1')
    for directory, providers in (
        (profile, {'nous': state} if profile_owns_state else {}),
        (root, {'nous': dict(state, refresh_token='synthetic-root-grant')}),
    ):
        (directory / 'auth.json').write_text(json.dumps(dict(
            version=1, active_provider='other-provider', providers=providers)))
    owner = profile if profile_owns_state else root
    untouched = root if profile_owns_state else profile
    original_other = (untouched / 'auth.json').read_bytes()
    original_owner = (owner / 'auth.json').read_bytes()
    replacement = _invoke_jwt() if usable_refresh else 'synthetic-invalid-jwt'
    requests = []

    def reply(request):
        assert request.method == 'POST'
        assert str(request.url) == 'https://portal.invalid/api/oauth/token'
        expected_grant = 'synthetic-old-grant' if profile_owns_state else 'synthetic-root-grant'
        assert request.headers['x-nous-refresh-token'] == expected_grant
        requests.append(request)
        return httpx.Response(200, json=dict(access_token=replacement,
            refresh_token='synthetic-rotated-grant', expires_in=3600, scope='inference:invoke'))

    monkeypatch.setattr(auth_nous, '_nous_http_client',
                        lambda *args: httpx.Client(transport=httpx.MockTransport(reply)))
    updates = []
    with auth._provider_state_transaction('nous') as (store, selected, source):
        assert source == owner / 'auth.json'
        assert (owner / 'auth.json').read_bytes() == original_owner

        def persist_rotated(updated, reason):
            auth._save_provider_state_to_source(store, 'nous', updated, source)
            updates.append(reason)

        def refresh():
            return auth_nous.refresh_nous_oauth_from_state(
                selected, force_refresh=True, on_state_update=persist_rotated)

        if usable_refresh:
            result = refresh()
            assert result['access_token'] == replacement
        else:
            with pytest.raises(auth.AuthError):
                refresh()
    assert len(requests) == 1
    assert updates == ['post_refresh_access_token']
    saved = json.loads((owner / 'auth.json').read_text())
    assert saved['providers']['nous']['refresh_token'] == 'synthetic-rotated-grant'
    assert saved['providers']['nous']['access_token'] == replacement
    assert saved['active_provider'] == 'other-provider'
    assert (untouched / 'auth.json').read_bytes() == original_other
