"""Consumers can await a durable owner's pending boot without acquiring boot authority."""

from types import SimpleNamespace

import pytest


@pytest.mark.platforms('any')
def test_worker_waits_for_queued_owner_boot_without_process_local_owner(monkeypatch):
    from agent import memory_provider
    from hermes_cli.local_runtime import binaries, detect, endpoint, ownership
    from hermes_constants import get_hermes_home

    owner = ownership.RuntimeOwner(get_hermes_home())
    queued = []
    monkeypatch.setattr(memory_provider, 'spawn_context_thread',
                        lambda target, **kwargs: SimpleNamespace(start=lambda: queued.append(target)))
    monkeypatch.setattr(ownership, '_OWNER', None)
    monkeypatch.setattr(binaries, 'installed_engine', lambda backend: SimpleNamespace())
    monkeypatch.setattr(detect, 'detect_server', lambda **kwargs: None)
    result = {'base_url': 'http://127.0.0.1:1/v1', 'api_key': 'fixture'}
    state = []
    monkeypatch.setattr(endpoint, '_state_endpoint', lambda: state[0] if state else None)
    monkeypatch.setattr(endpoint.time, 'sleep', lambda seconds: state.append(result))
    try:
        assert owner.request_boot() and len(queued) == 1
        assert endpoint.resolve_llamacpp_endpoint({'local_runtime': {'enabled': True}}) == result
    finally:
        owner.close()


@pytest.mark.parametrize('enabled', [False, None])
def test_observing_root_boot_does_not_require_consumer_enablement(monkeypatch, enabled):
    from hermes_cli.local_runtime import boot_status, detect, endpoint, ownership
    from hermes_constants import get_hermes_home

    owner = ownership.RuntimeOwner(get_hermes_home())
    owner._boot_status.begin()
    monkeypatch.setattr(ownership, '_OWNER', None)
    monkeypatch.setattr(detect, 'detect_server', lambda **kwargs: None)
    result = {'base_url': 'http://127.0.0.1:1/v1', 'api_key': 'fixture'}
    states = []
    monkeypatch.setattr(endpoint, '_state_endpoint', lambda: states[0] if states else None)
    monkeypatch.setattr(endpoint.time, 'sleep', lambda seconds: states.append(result))
    try:
        assert boot_status.owner_boot_pending()
        assert endpoint.resolve_llamacpp_endpoint({'local_runtime': {'enabled': enabled}}) == result
    finally:
        owner._boot_status.end()
    assert not boot_status.owner_boot_pending()


@pytest.mark.parametrize('finish', ['success', 'failed', 'start_failed', 'closed'])
def test_boot_observation_ends_with_owner_request(monkeypatch, finish):
    from agent import memory_provider
    from hermes_cli.local_runtime import boot_status, detect, endpoint, ownership
    from hermes_constants import get_hermes_home

    owner = ownership.RuntimeOwner(get_hermes_home())
    queued = []
    def start(target):
        if finish == 'start_failed':
            raise RuntimeError('fixture thread start failed')
        queued.append(target)
    monkeypatch.setattr(memory_provider, 'spawn_context_thread',
                        lambda target, **kwargs: SimpleNamespace(start=lambda: start(target)))
    def ensure(**kwargs):
        if finish == 'closed':
            assert owner.stopping.is_set()
            return
        assert boot_status.owner_boot_pending()
        if finish == 'failed':
            raise RuntimeError('fixture boot failed')
    monkeypatch.setattr(owner, '_ensure', ensure)
    if finish == 'start_failed':
        with pytest.raises(RuntimeError, match='thread start failed'):
            owner.request_boot()
    else:
        assert owner.request_boot() and boot_status.owner_boot_pending()
        if finish == 'closed':
            owner.close()
        else:
            queued.pop()()
    assert not boot_status.owner_boot_pending()
    if finish == 'closed':
        assert not owner.request_boot()
        queued.pop()()  # A queued worker cannot republish after the owner closes.
        assert not boot_status.owner_boot_pending()
    monkeypatch.setattr(ownership, '_OWNER', None)
    monkeypatch.setattr(endpoint, '_state_endpoint', lambda: None)
    monkeypatch.setattr(endpoint, '_boot_in_flight', lambda config: True)
    monkeypatch.setattr(detect, 'detect_server', lambda **kwargs: None)
    monkeypatch.setattr(endpoint.time, 'sleep', lambda seconds: pytest.fail('settled boot wait'))
    assert endpoint.resolve_llamacpp_endpoint() is None


@pytest.mark.parametrize('damage', ['birth', 'root', 'executable', 'json'])
def test_stale_or_foreign_boot_record_is_not_evidence(damage):
    import json
    from hermes_cli.local_runtime import boot_status
    from hermes_constants import get_hermes_home

    status = boot_status.BootStatus(get_hermes_home())
    status.begin()
    try:
        assert boot_status.owner_boot_pending()
        data = json.loads(status.path.read_text())
        if damage == 'birth':
            data['create_time'] = 1
            data['start_time'] = 1
        elif damage == 'root':
            data['home'] = str(get_hermes_home() / 'other')
        elif damage == 'executable':
            data['executable'] = 'not-the-live-executable'
        status.path.write_text('{bad json' if damage == 'json' else json.dumps(data))
        assert not boot_status.owner_boot_pending()
    finally:
        status.end()


@pytest.mark.platforms('any')
def test_native_consumer_observes_owner_boot_in_another_process(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    from hermes_cli.local_runtime import boot_status
    from hermes_constants import get_hermes_home

    status = boot_status.BootStatus(get_hermes_home())
    env = {key: value for key, value in os.environ.items()
           if key in {'PATH', 'SYSTEMROOT', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR'}}
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), HERMES_HOME=str(get_hermes_home()))
    script = ('import sys; from pathlib import Path; '
              'assert Path.home() == Path(sys.argv[1]), "child user home must be isolated"; '
              'from hermes_cli.local_runtime.boot_status import owner_boot_pending; '
              'from hermes_cli.local_runtime.ownership import current_owner; '
              'assert current_owner() is None; print(owner_boot_pending())')
    def observe():
        result = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                                cwd=Path(__file__).resolve().parents[2],
                                env=env, capture_output=True, text=True, timeout=15, check=False)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()
    status.begin()
    try:
        assert observe() == 'True'
    finally:
        status.end()
    assert observe() == 'False'


def test_explicit_owner_boot_is_visible_before_server_publication(monkeypatch):
    from hermes_cli.local_runtime import boot_status, bootstrap, detect, endpoint, ownership
    from hermes_cli.web_routers import local_models
    from hermes_constants import get_hermes_home

    owner = ownership.RuntimeOwner(get_hermes_home())
    monkeypatch.setattr(ownership, '_OWNER', owner)
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    monkeypatch.setattr(detect, 'detect_server', lambda **kwargs: None)
    state = []
    result = {'base_url': 'http://127.0.0.1:1/v1', 'api_key': 'fixture'}
    monkeypatch.setattr(endpoint, '_state_endpoint', lambda: state[0] if state else None)
    monkeypatch.setattr(endpoint.time, 'sleep', lambda seconds: state.append(result))
    config = {'local_runtime': {'enabled': False, 'backend': 'cpu'}}
    calls = []
    def boot(section, actual_owner, cancel, *, force):
        calls.append((section, actual_owner, force))
        assert boot_status.owner_boot_pending(), 'explicit owner boot must be observable'
        with monkeypatch.context() as consumer:
            consumer.setattr(ownership, '_OWNER', None)
            assert endpoint.resolve_llamacpp_endpoint(config) == result
        return SimpleNamespace()
    monkeypatch.setattr(bootstrap, '_boot_owned_runtime', boot)
    try:
        assert local_models._start_local_server(config, 'fixture failed') is not None
    finally:
        owner.close()
    assert calls == [(config['local_runtime'], owner, True)]
    assert not boot_status.owner_boot_pending()
