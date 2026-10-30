"""Offline effect fences for temporary-owner breadcrumb regression tests."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

class EffectDenied(BaseException):
    pass


def _absent(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise AssertionError('unexpected bootstrap metadata: ' + str(path))


def _preflight():
    root = Path(__file__).resolve().parents[2]
    home = Path(os.environ['HERMES_HOME']).resolve()
    temporary = Path(tempfile.gettempdir()).resolve()
    assert temporary != Path('/')
    assert home.is_relative_to(temporary)
    assert os.environ['HERMES_DISABLE_LAZY_INSTALLS'] == '1'
    assert os.environ.get('HERMES_TEST_ISOLATION')
    assert not os.environ.get('HERMES_INSTALL_ROOT')
    assert not os.environ.get('HERMES_RUNTIME_DIR')
    assert not hasattr(sys, '_hermes_startup_writer_handoff')
    assert not getattr(sys, '_hermes_pin_default_home', False)
    for path in [root / '.hermes-update-zip-swap',
                 root / '.update-incomplete', root / '.lazy-refresh-incomplete',
                 root / 'install-stamp.json', root / 'manifest.json',
                 root.parent / 'manifest.json']:
        _absent(path)
    from hermes_cli._early_recovery import interrupted_pull_marker
    _absent(interrupted_pull_marker(root))
    from pm.environments import install_state_dir
    state = install_state_dir(root).resolve()
    assert state.is_relative_to(home)
    _absent(state)
    from hermes_constants import get_default_hermes_root, _get_platform_default_hermes_home, get_hermes_home
    assert get_hermes_home().resolve() == home
    for candidate in {home, get_default_hermes_root().resolve(),
                      _get_platform_default_hermes_home().resolve()}:
        assert any(candidate.is_relative_to(base) for base in (home, temporary))
        for name in ('.startup-hold.json', '.drain_request.json'):
            _absent(candidate / name)
    from hermes_cli.startup_hold import _process_writer_purpose
    assert _process_writer_purpose(sys.argv[1:]) is None
    return state


def _fence_unrelated_effects(monkeypatch):
    _preflight()
    attempted = []
    def deny(kind):
        def blocked(*args, **kwargs):
            attempted.append(kind)
            raise EffectDenied('excluded component effect: ' + kind)
        return blocked
    monkeypatch.setattr(socket, 'getaddrinfo', deny('dns'))
    monkeypatch.setattr(socket.socket, 'connect', deny('connect'))
    monkeypatch.setattr(socket.socket, 'connect_ex', deny('connect_ex'))
    monkeypatch.setattr(subprocess, 'Popen', deny('process'))
    for name in ('system', 'popen', 'execv', 'execve', 'execvp', 'execvpe',
                 'posix_spawn', 'posix_spawnp', 'fork', 'kill'):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, deny('process:' + name))
    from hermes_cli import env_loader
    monkeypatch.setattr(env_loader, 'load_hermes_dotenv', lambda *a, **k: [])
    from gateway import session_policy, session_policy_refresh
    async def no_refresh(*args, **kwargs):
        return None
    monkeypatch.setattr(session_policy, 'refresh_profile_hooks', lambda *a, **k: None)
    monkeypatch.setattr(session_policy_refresh, 'refresh_resumed_policy', no_refresh)
    return attempted
