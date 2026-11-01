"""A cooperating auth writer cannot rotate between final validation and publication."""
import json
from pathlib import Path
import threading

import pytest

pytestmark = pytest.mark.platforms('posix')


def test_auth_writer_waits_until_candidate_publication_finishes(tmp_path, monkeypatch):
    from hermes_cli import auth
    from hermes_cli.auth_nous_adoption import prepare_nous_adoption, commit_current_adoption
    from tests.hermes_cli.test_auth_nous_provider import _invoke_jwt
    home = tmp_path / 'profile'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = home / 'auth.json'
    state = dict(providers={'nous': dict(access_token=_invoke_jwt(), scope='inference:invoke',
                                        refresh_token='synthetic-old')})
    path.write_text(json.dumps(state))
    candidate = prepare_nous_adoption(profile_home=home, model='test-model')
    assert candidate is not None
    contention = threading.Event()
    written = threading.Event()
    main_thread = threading.get_ident()
    original_lock = auth._kernel_lock

    def observed_lock(file, acquire):
        try:
            return original_lock(file, acquire)
        except BlockingIOError:
            if acquire and threading.get_ident() != main_thread:
                contention.set()
            raise

    monkeypatch.setattr(auth, '_kernel_lock', observed_lock)

    def rotate():
        with auth._auth_store_lock():
            state['providers']['nous']['refresh_token'] = 'synthetic-new'
            path.write_text(json.dumps(state))
        written.set()

    writer = threading.Thread(target=rotate)
    publications = []

    def publish():
        writer.start()
        assert contention.wait(10), 'writer did not encounter the held credential lock'
        assert not written.is_set()
        publications.append(json.loads(path.read_text())['providers']['nous']['refresh_token'])

    try:
        assert commit_current_adoption(candidate, publish)
    finally:
        if writer.ident is not None:
            writer.join(timeout=10)
    assert written.is_set() and not writer.is_alive()
    assert publications == ['synthetic-old']
    assert not commit_current_adoption(candidate, lambda: pytest.fail('stale candidate published'))
    assert json.loads(path.read_text())['providers']['nous']['refresh_token'] == 'synthetic-new'
