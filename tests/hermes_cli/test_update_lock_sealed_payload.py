"""Sealed native payloads retain checkout custody without changing signed resources."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys

import pytest

from hermes_cli import update_lock


def _payload(tmp_path):
    app = tmp_path / 'Hermes.app'
    payload = app / 'Contents' / 'Resources' / 'agent-payload'
    root = payload / 'hermes-agent'
    root.mkdir(parents=True)
    runtime = {'repoDir': 'hermes-agent', 'toolsDir': 'tools',
               'storePython': 'tools/python/bin/python3', 'sitePackages': 'venv/site-packages',
               'commands': {'hermes': 'bin/hermes'}}
    (payload / 'manifest.json').write_text(json.dumps({
        'schema': 1, 'target': 'darwin-arm64', 'repo': 'hermes-agent', 'venv': 'venv',
        'store': 'tools', 'runtime': runtime, 'ref': 'a' * 40}))
    (root / 'install-stamp.json').write_text(json.dumps({
        'schemaVersion': 1, 'commit': 'a' * 40, 'dirty': False,
        'payload': 'bundled', 'distribution': 'desktop-app', 'updateMechanism': 'external',
        'runtime': runtime}))
    return app, root


def _inventory(app):
    return {str(p.relative_to(app)): (p.stat().st_mode, p.stat().st_ino,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
            for p in (app, *app.rglob('*'))}


@pytest.fixture(autouse=True)
def _no_leaked_custody():
    assert update_lock._HELD is None
    yield
    while update_lock._HELD is not None:
        update_lock._release_checkout()


def test_sealed_acquisition_and_release_leave_app_unchanged(tmp_path):
    app, root = _payload(tmp_path)
    before = _inventory(app)
    assert update_lock._acquire_checkout(root) is None
    assert not update_lock.checkout_lock_path(root).is_relative_to(app)
    assert update_lock.checkout_lock_held(root)
    update_lock._release_checkout()
    assert not update_lock.checkout_lock_held(root)
    assert _inventory(app) == before


def test_readonly_payload_uses_same_gate_across_homes_and_aliases(tmp_path, monkeypatch):
    app, root = _payload(tmp_path)
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    for p in (app, *app.rglob('*')):
        p.chmod(0o555 if p.is_dir() else 0o444)
    before = _inventory(app)
    try:
        monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'one'))
        first = update_lock.checkout_lock_path(root)
        assert update_lock._acquire_checkout(root) is None
        monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'two' / 'profiles' / 'work'))
        assert update_lock.checkout_lock_path(alias) == first
        assert update_lock._acquire_checkout(alias) is None
        update_lock._release_checkout()
        update_lock._release_checkout()
        assert _inventory(app) == before
    finally:
        for p in (app, *app.rglob('*')):
            p.chmod(0o755 if p.is_dir() else 0o644)


def _child(root, code, **kwargs):
    repo = str(Path(update_lock.__file__).resolve().parents[1])
    program = ('import os,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
               'from hermes_cli import update_lock;root=Path(sys.argv[2]);'
               'update_lock.CHECKOUT_CONTENTION_WAIT_SECONDS=0;' + code)
    return subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', program, repo, str(root)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, **kwargs)


def test_foreign_home_contender_cannot_enter_same_canonical_app(tmp_path):
    from tests.waiting import wait_until

    _, root = _payload(tmp_path)
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    ready = tmp_path / 'owner-ready'
    owner = _child(root, 'assert update_lock._acquire_checkout(root) is None;'
                   f'Path({str(ready)!r}).touch();sys.stdin.readline();'
                   'update_lock._release_checkout()')
    contender = None
    try:
        wait_until(ready.exists, timeout=20, what='sibling owner acquired custody')
        env = dict(os.environ, HERMES_HOME=str(tmp_path / 'foreign' / 'profiles' / 'work'))
        contender = _child(alias, 'print(update_lock._acquire_checkout(root) is not None)', env=env)
        out, err = contender.communicate(timeout=20)
        assert contender.returncode == 0, err
        assert out.strip() == 'True'
        _, err = owner.communicate('release\n', timeout=20)
        assert owner.returncode == 0, err
    finally:
        for child in (owner, contender):
            if child is not None and child.poll() is None:
                child.kill()
                child.wait()


@pytest.mark.platforms('posix')
def test_inherited_custody_survives_parent_release_and_manifest_damage(tmp_path):
    _, root = _payload(tmp_path)
    assert update_lock._acquire_checkout(root) is None
    child = _child(root, 'assert update_lock._acquire_checkout(root) is None;'
                   'print("joined",flush=True);sys.stdin.readline();'
                   'print(update_lock._acquire_checkout(root) is not None,flush=True);'
                   'update_lock._release_checkout()', pass_fds=update_lock.checkout_lock_fds(root))
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=20), 'child did not join custody'
            assert child.stdout.readline().strip() == 'joined'
        update_lock._release_checkout()
        assert update_lock.checkout_lock_held(root)
        (root.parent / 'manifest.json').write_text('{}')
        out, err = child.communicate('recheck\n', timeout=20)
        assert child.returncode == 0, err
        assert out.strip() == 'True'
        assert update_lock.checkout_lock_held(root), 'invalid package stays uncertain after child exit'
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


@pytest.mark.parametrize('damage', ['lost_receipt', 'replaced_gate', 'legacy_embedded_gate'])
def test_uncertain_gate_never_self_heals_or_mutates_app(tmp_path, damage):
    app, root = _payload(tmp_path)
    gate = update_lock.checkout_lock_path(root)
    assert update_lock._acquire_checkout(root) is None
    update_lock._release_checkout()
    if damage == 'lost_receipt':
        gate.with_name(gate.name + '.identity').unlink()
    elif damage == 'replaced_gate':
        gate.unlink()
    else:
        (root / '.hermes-update.lock').write_text('legacy consumer')
    before = _inventory(app)
    assert update_lock.checkout_lock_held(root)
    assert update_lock._acquire_checkout(root) is not None
    if damage == 'replaced_gate':
        assert not gate.exists(), 'lost custody must not recreate a gate with a reusable inode'
    assert _inventory(app) == before


def test_receipt_racing_exclusive_creation_cannot_rebind_reused_identity(tmp_path, monkeypatch):
    from hermes_cli import update_lock_payload

    _, root = _payload(tmp_path)
    gate = update_lock.checkout_lock_path(root)
    receipt = gate.with_name(gate.name + '.identity')
    real_open = os.open
    created = []

    def racing_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if Path(path) == gate and flags & os.O_EXCL:
            created.append(fd)
            found = os.fstat(fd)
            # Even a receipt naming this exact inode cannot authorize our replacement.
            receipt.write_bytes(update_lock_payload.identity(root)
                                + f'{found.st_dev}:{found.st_ino}\n'.encode())
        return fd

    monkeypatch.setattr(os, 'open', racing_open)
    assert update_lock._acquire_checkout(root) is not None
    assert update_lock._HELD is None
    assert len(created) == 1
    with pytest.raises(OSError):
        os.fstat(created[0])
    assert gate.is_file() and receipt.is_file(), 'refusal never unlinks shared custody evidence'


@pytest.mark.parametrize('kind', ['corrupt', 'dangling', 'directory', 'unreadable'])
def test_missing_gate_with_uncertain_receipt_is_not_recreated(tmp_path, monkeypatch, kind):
    _, root = _payload(tmp_path)
    gate = update_lock.checkout_lock_path(root)
    receipt = gate.with_name(gate.name + '.identity')
    if kind == 'corrupt':
        receipt.write_text('not a receipt')
    elif kind == 'dangling':
        receipt.symlink_to(tmp_path / 'absent')
    elif kind == 'directory':
        receipt.mkdir()
    else:
        real_lstat = Path.lstat
        def guarded_lstat(path):
            if path == receipt:
                raise PermissionError('fixture uncertain receipt')
            return real_lstat(path)
        monkeypatch.setattr(Path, 'lstat', guarded_lstat)
    assert update_lock._acquire_checkout(root) is not None
    assert not gate.exists()


@pytest.mark.parametrize('race', ['creator', 'creator_then_unlink', 'readonly_then_unlink', 'readonly'])
def test_sealed_existing_open_and_creator_races_never_recreate(tmp_path, monkeypatch, race):
    _, root = _payload(tmp_path)
    gate = update_lock.checkout_lock_path(root)
    if race.startswith('readonly'):
        assert update_lock._acquire_checkout(root) is None
        update_lock._release_checkout()
    real_open = os.open
    creations = []

    def racing_open(path, flags, *args, **kwargs):
        if Path(path) == gate:
            if flags & os.O_EXCL:
                creations.append(1)
                fd = real_open(path, flags, *args, **kwargs)
                os.close(fd)
                if race == 'creator_then_unlink':
                    gate.unlink()
                raise FileExistsError('fixture creator won')
            if race.startswith('readonly') and flags & os.O_RDWR:
                raise PermissionError('fixture readonly gate')
            if race == 'readonly_then_unlink':
                raise FileNotFoundError('fixture gate lost during readonly fallback')
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', racing_open)
    refused = update_lock._acquire_checkout(root)
    expected = race == 'creator' or (race == 'readonly' and sys.platform != 'win32')
    assert (refused is None) == expected
    assert len(creations) == (1 if race.startswith('creator') else 0)
    if race == 'creator_then_unlink':
        assert not gate.exists()


@pytest.mark.parametrize('damage', ['missing', 'malformed', 'wrong_repo', 'stamp_mismatch'])
def test_known_sealed_layout_fails_closed_before_source_write(tmp_path, damage):
    app, root = _payload(tmp_path)
    manifest = root.parent / 'manifest.json'
    if damage == 'missing':
        manifest.unlink()
    elif damage == 'malformed':
        manifest.write_text('{')
    else:
        row = json.loads(manifest.read_text())
        row['repo' if damage == 'wrong_repo' else 'ref'] = '../other' if damage == 'wrong_repo' else 'b' * 40
        manifest.write_text(json.dumps(row))
    before = _inventory(app)
    assert update_lock._acquire_checkout(root) is not None
    assert update_lock.checkout_lock_held(root), 'uncertain sealed custody must never answer free'
    assert _inventory(app) == before


def test_replaced_manifest_cannot_split_or_rebind_existing_gate(tmp_path):
    app, root = _payload(tmp_path)
    gate = update_lock.checkout_lock_path(root)
    assert update_lock._acquire_checkout(root) is None
    update_lock._release_checkout()
    manifest = root.parent / 'manifest.json'
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(manifest.read_bytes())
    replacement.replace(manifest)
    before = _inventory(app)
    assert update_lock.checkout_lock_path(root) == gate
    assert update_lock._acquire_checkout(root) is not None
    assert update_lock.checkout_lock_held(root)
    assert _inventory(app) == before


@pytest.mark.parametrize('refusal', ['runtime_busy', 'recovery_pending'])
def test_startup_checkout_refusal_preserves_sealed_resources(tmp_path, monkeypatch, refusal):
    from gateway import drain_startup
    from hermes_cli import runtime_state, startup_hold
    from hermes_state_runtime import RuntimeStoreError

    app, root = _payload(tmp_path)
    monkeypatch.setattr(drain_startup, '_checkout_root', lambda: root)

    @contextmanager
    def runtime_lock(*args, **kwargs):
        yield refusal != 'runtime_busy'

    monkeypatch.setattr(runtime_state, 'runtime_lock', runtime_lock)
    monkeypatch.setattr(startup_hold, 'recovery_pending', lambda root: refusal == 'recovery_pending')
    before = _inventory(app)
    with pytest.raises(RuntimeStoreError, match='startup_hold_'):
        with drain_startup._checkout_custody():
            pytest.fail('refused transaction entered')
    assert update_lock._HELD is None
    assert _inventory(app) == before


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('kind', ['fifo', 'symlink', 'hardlink'])
def test_untrusted_gate_objects_refuse_promptly_without_following(tmp_path, kind):
    app, root = _payload(tmp_path)
    gate = update_lock.checkout_lock_path(root)
    unrelated = tmp_path / 'unrelated'
    unrelated.write_text('must remain intact')
    if kind == 'fifo':
        os.mkfifo(gate)
    elif kind == 'symlink':
        gate.symlink_to(unrelated)
    else:
        os.link(unrelated, gate)
    before = _inventory(app)
    child = _child(root, 'print(update_lock.checkout_lock_held(root));'
                   'print(update_lock._acquire_checkout(root) is not None)')
    try:
        out, err = child.communicate(timeout=20)
        assert child.returncode == 0, err
        assert out.splitlines() == ['True', 'True']
        assert unrelated.read_text() == 'must remain intact'
        assert _inventory(app) == before
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


@pytest.mark.parametrize('field', ['manifest', 'stamp'])
def test_boolean_schema_is_not_a_payload_version(tmp_path, field):
    app, root = _payload(tmp_path)
    path = root.parent / 'manifest.json' if field == 'manifest' else root / 'install-stamp.json'
    row = json.loads(path.read_text())
    row['schema' if field == 'manifest' else 'schemaVersion'] = True
    path.write_text(json.dumps(row))
    before = _inventory(app)
    assert update_lock._acquire_checkout(root) is not None
    assert update_lock.checkout_lock_held(root)
    assert _inventory(app) == before


@pytest.mark.platforms('posix')
def test_source_marker_probe_delegates_free_held_and_invalid_sealed_identity(tmp_path):
    app, root = _payload(tmp_path)
    before = _inventory(app)
    script = Path(update_lock.__file__).resolve().parents[1] / 'scripts/desktop-update/marker.sh'

    def probe():
        return subprocess.check_output(['bash', '-c',
            'source "$0"; if checkout_lock_held; then echo held; else echo free; fi', str(script)],
            env=dict(os.environ, INSTALL_ROOT=str(root)), text=True, timeout=20).strip()

    assert probe() == 'free'
    assert update_lock._acquire_checkout(root) is None
    assert probe() == 'held'
    update_lock._release_checkout()
    assert probe() == 'free'
    assert _inventory(app) == before
    (root.parent / 'manifest.json').unlink()
    assert probe() == 'held'
    assert not (root / '.hermes-update.lock').exists()


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('aliased', [False, True])
def test_sealed_shell_without_external_tools_never_falls_back_free(tmp_path, aliased):
    _, root = _payload(tmp_path)
    selected = root
    if aliased:
        selected = tmp_path / 'source-alias'
        selected.symlink_to(root, target_is_directory=True)
    empty = tmp_path / 'empty-path'
    empty.mkdir()
    script = Path(update_lock.__file__).resolve().parents[1] / 'scripts/desktop-update/marker.sh'
    result = subprocess.run(['bash', '-c',
        'source "$1"; PATH="$2"; checkout_lock_path; '
        'if checkout_lock_held; then echo held; else echo free; fi',
        'probe', str(script), str(empty)], env=dict(os.environ, INSTALL_ROOT=str(selected)),
        capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0
    assert result.stderr == ''
    assert result.stdout.splitlines() == [str(update_lock.checkout_lock_path(root)), 'held']
    assert not (root / '.hermes-update.lock').exists()


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('exit_code', [1, 2, 3])
@pytest.mark.parametrize('layout', ['zip', 'git', 'empty_git', 'invalid_git'])
def test_checkout_path_reports_its_own_status_in_failure_exit_trap(tmp_path, exit_code, layout):
    root = tmp_path / 'checkout with spaces'
    root.mkdir()
    if layout == 'git':
        (root / '.git').mkdir()
    elif layout != 'zip':
        (root / '.git').write_text('' if layout == 'empty_git' else 'not a git pointer')
    script = Path(update_lock.__file__).resolve().parents[1] / 'scripts/desktop-update/marker.sh'
    program = r'''source "$1"
cleanup() {
    named="$(checkout_lock_path)"; resolved=$?
    printf '%s\n%s\n' "$resolved" "$named"
    if checkout_lock_held; then echo held; else echo free; fi
}
trap cleanup EXIT
exit "$2"
'''
    result = subprocess.run(['bash', '-c', program, 'probe', str(script), str(exit_code)],
                            env=dict(os.environ, INSTALL_ROOT=str(root)), capture_output=True,
                            text=True, timeout=20, check=False)
    assert result.returncode == exit_code
    assert result.stdout.splitlines() == ['0', str(update_lock.checkout_lock_path(root)), 'free']
    assert result.stderr == ''


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('exit_code', [1, 2, 3])
@pytest.mark.parametrize('state', ['free', 'held', 'invalid'])
def test_sealed_probe_keeps_its_verdict_in_failure_exit_trap(tmp_path, exit_code, state):
    _, root = _payload(tmp_path)
    if state == 'held':
        assert update_lock._acquire_checkout(root) is None
    elif state == 'invalid':
        (root.parent / 'manifest.json').write_text('{}')
    script = Path(update_lock.__file__).resolve().parents[1] / 'scripts/desktop-update/marker.sh'
    program = r'''source "$1"
cleanup() {
    named="$(checkout_lock_path)"; resolved=$?
    printf '%s\n%s\n' "$resolved" "$named"
    if checkout_lock_held; then echo held; else echo free; fi
}
trap cleanup EXIT
exit "$2"
'''
    result = subprocess.run(['bash', '-c', program, 'probe', str(script), str(exit_code)],
                            env=dict(os.environ, INSTALL_ROOT=str(root)), capture_output=True,
                            text=True, timeout=20, check=False)
    assert result.returncode == exit_code
    assert result.stdout.splitlines() == ['0', str(update_lock.checkout_lock_path(root)),
                                          'free' if state == 'free' else 'held']
    assert result.stderr == ''
