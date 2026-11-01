"""Non-refreshing adoption never repairs auth or escapes its bound profile."""
import json
from pathlib import Path
import time

import pytest

pytestmark = pytest.mark.platforms('posix')


@pytest.fixture
def owned_auth(tmp_path, monkeypatch):
    from hermes_cli import auth, auth_nous
    from tests.hermes_cli.test_auth_nous_provider import _invoke_jwt
    home = tmp_path / 'profile'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail('read-only adoption crossed an auth mutation/refresh/root lookup boundary')

    for name in ('_load_auth_store', '_load_global_auth_store', '_save_auth_store',
                 '_auth_store_lock', 'resolve_nous_runtime_credentials'):
        monkeypatch.setattr(auth, name, forbidden)
    monkeypatch.setattr(auth_nous, '_nous_http_client', forbidden)
    state = dict(access_token=_invoke_jwt(), refresh_token='synthetic-refresh', scope='inference:invoke')
    return home, dict(providers={'nous': state})


@pytest.mark.parametrize('case', ['valid', 'expired', 'scope', 'endpoint', 'dead', 'cooldown',
                                  'ambiguous', 'stale-pool', 'malformed', 'root-only', 'guest', 'guest-state', 'guest-jwt'])
def test_adoption_is_read_only_and_refuses_unsupported_state(owned_auth, case):
    from hermes_cli.auth_nous_adoption import prepare_nous_adoption
    from tests.hermes_cli.test_auth_nous_provider import _invoke_jwt
    home, store = owned_auth
    state = store['providers']['nous']
    row = dict(source='device_code', access_token=state['access_token'], scope=state['scope'], last_status='ok')
    store['credential_pool'] = {'nous': [row]}
    from tests.hermes_cli.test_auth_nous_provider import _jwt_with_claims

    def set_token(token):
        state['access_token'] = row['access_token'] = token

    mutations = {
        'expired': lambda: set_token(_invoke_jwt(seconds=-1)),
        'scope': lambda: (set_token(_invoke_jwt(scope='billing:manage')),
                          state.update(scope='billing:manage')),
        'endpoint': lambda: state.update(inference_base_url='https://untrusted.invalid/v1'),
        'dead': lambda: row.update(last_status='dead'),
        'cooldown': lambda: row.update(model_cooldowns={'test-model': time.time() + 3600}),
        'ambiguous': lambda: store['credential_pool']['nous'].append(dict(row)),
        'stale-pool': lambda: row.update(access_token='synthetic-stale'),
        'guest': lambda: state.update(anon_token='synthetic-guest'),
        'guest-state': lambda: state.update(auth_method='anonymous'),
        'guest-jwt': lambda: set_token(_jwt_with_claims(dict(sub='synthetic-guest',
            account_tier='anonymous', scope='inference:invoke', exp=int(time.time()) + 3600))),
    }
    if case in mutations:
        mutations[case]()
    if case == 'root-only':
        (home.parent / 'auth.json').write_text(json.dumps(store))
        store = {'providers': {}}
    raw = b'broken-json' if case == 'malformed' else json.dumps(store).encode()
    (home / 'auth.json').write_bytes(raw)
    before = {str(p): p.read_bytes() for p in home.parent.rglob('*') if p.is_file()}
    candidate = prepare_nous_adoption(profile_home=home, model='test-model')
    if case == 'valid':
        assert candidate.api_key == state['access_token']
        assert candidate.api_key not in repr(candidate)
    else:
        assert candidate is None
    assert {str(p): p.read_bytes() for p in home.parent.rglob('*') if p.is_file()} == before


def test_adoption_revalidates_profile_generation_and_refuses_symlink(owned_auth):
    from hermes_cli.auth_nous_adoption import prepare_nous_adoption, adoption_is_current
    home, store = owned_auth
    path = home / 'auth.json'
    path.write_text(json.dumps(store))
    candidate = prepare_nous_adoption(profile_home=home, model='test-model')
    assert candidate is not None and adoption_is_current(candidate)
    assert prepare_nous_adoption(profile_home=home.parent / 'sibling', model='test-model') is None
    store['providers']['nous']['refresh_token'] = 'synthetic-rotated'
    path.write_text(json.dumps(store))
    assert not adoption_is_current(candidate)
    target = home.parent / 'other.json'
    path.rename(target)
    path.symlink_to(target)
    assert prepare_nous_adoption(profile_home=home, model='test-model') is None


def test_replacement_symlink_and_fifo_refuse_without_reading(owned_auth, monkeypatch):
    import os
    from hermes_cli.auth_nous_adoption import prepare_nous_adoption
    home, store = owned_auth
    path = home / 'auth.json'
    path.write_text(json.dumps(store))
    other = home.parent / 'other.json'
    other.write_text(json.dumps(store))
    original_open = os.open

    def replace_then_open(target, flags, *args, **kwargs):
        path.unlink()
        path.symlink_to(other)
        return original_open(target, flags, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', replace_then_open)
        assert prepare_nous_adoption(profile_home=home, model='test-model') is None
    path.unlink()
    os.mkfifo(path)
    assert prepare_nous_adoption(profile_home=home, model='test-model') is None
