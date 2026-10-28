"""Qualified generations advance only after complete, durable retirement proof."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from hermes_cli.local_runtime import bootstrap, generation_evidence as g, generation_history as h, reconciliation


def _final(number):
    return json.dumps(dict(pid=42 + number, owner_pid=41, create_time=42.0 + number,
                           owner_create_time=41.0, executable='/fixture/engine')).encode()


def _write(path, raw):
    path.write_bytes(raw)
    path.chmod(0o600)


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    facts = {'host': '11111111-1111-4111-8111-111111111111',
             'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    for module in (bootstrap, reconciliation):
        monkeypatch.setattr(module, 'runtimes_root', lambda: root)
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    monkeypatch.setattr(reconciliation, '_native_identity', host)
    monkeypatch.setattr(reconciliation, '_volume_identity', volume)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = g.reserve(root, None, host, volume)
        g.authorize(root, ticket, host, volume)
        for raw in (b'{"stage":"intent"}', _final(0)):
            g.bind_publication(root, ticket, raw, host, volume)
            _write(root / 'server.json', raw)
    digest = hashlib.sha256(_final(0)).hexdigest()
    checkpoint = reconciliation.checkpoint(expected_sha256=digest)
    facts['boot'] = '44444444-4444-4444-8444-444444444444'
    retired = reconciliation.reconcile(expected_sha256=digest, checkpoint_id=checkpoint['checkpoint_id'])
    return root, facts, host, volume, ticket, digest, retired


def _advance(root, host, volume, number):
    path = root / 'server.json'
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        assert reconciliation.retirement_matches(path)
        ticket = g.reserve(root, hashlib.sha256(path.read_bytes()).hexdigest(), host, volume)
        g.authorize(root, ticket, host, volume)
        g.publish_intent(root, ticket, host, volume)
        final = _final(number)
        g.bind_publication(root, ticket, final, host, volume)
        _write(path, final)
        g.verify_current_publication(root, ticket, final, 2, host, volume)
    return ticket


@pytest.mark.platforms('macos')
def test_three_generations_consume_real_retirement_and_preserve_ancestors(case):
    root, facts, host, volume, first, _, retired = case
    directory = root / ('.generation-' + first)
    preserved = {p: p.read_bytes() for p in directory.iterdir()}
    second = _advance(root, host, volume, 1)
    digest = hashlib.sha256(_final(1)).hexdigest()
    checkpoint = reconciliation.checkpoint(expected_sha256=digest)
    facts['boot'] = '55555555-5555-4555-8555-555555555555'
    reconciliation.reconcile(expected_sha256=digest, checkpoint_id=checkpoint['checkpoint_id'])
    third = _advance(root, host, volume, 2)
    assert len({first, second, third}) == 3
    assert all(p.read_bytes() == raw for p, raw in preserved.items())
    assert Path(retired['recovery_path']).read_bytes() == _final(0)
    assert (root / 'server.json').read_bytes() == _final(2)


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('change', ['same_boot', 'no_retirement', 'first_only', 'torn_first',
                                    'no_authorization', 'extra', 'changed_state', 'source',
                                    'volume', 'backup', 'checkpoint', 'retirement', 'limit'])
def test_incomplete_or_damaged_predecessor_never_advances(case, monkeypatch, change):
    root, facts, host, volume, ticket, digest, retired = case
    directory = root / ('.generation-' + ticket)
    changes = {
        'same_boot': lambda: facts.update(boot='22222222-2222-4222-8222-222222222222'),
        'no_retirement': lambda: Path(retired['receipt_path']).unlink(),
        'first_only': lambda: (directory / 'publication-2.json').unlink(),
        'torn_first': lambda: _write(directory / 'publication.json', b'{'),
        'no_authorization': lambda: (directory / 'authorized.json').unlink(),
        'extra': lambda: (root / '.generation-unknown').mkdir(),
        'changed_state': lambda: _write(root / 'server.json', _final(8)),
        'source': lambda: monkeypatch.setattr(g, '_source_identity', lambda: {'changed': True}),
        'volume': lambda: monkeypatch.setattr(g, '_root', lambda *a: {'changed': True}),
        'backup': lambda: _write(Path(retired['recovery_path']), b'{}'),
        'checkpoint': lambda: _write(root / ('.router-checkpoint-' + digest + '.json'), b'{}'),
        'retirement': lambda: _write(Path(retired['receipt_path']), b'{}'),
        'limit': lambda: monkeypatch.setattr(h, 'MAX_GENERATIONS', 1),
    }
    changes[change]()
    before = set(root.iterdir())
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        with pytest.raises(RuntimeError, match='prior_generation_requires_inspection'):
            g.reserve(root, hashlib.sha256((root / 'server.json').read_bytes()).hexdigest(), host, volume)
    assert set(root.iterdir()) == before


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('fault', ['prior_link', 'reservation', 'stage_write', 'stage_fsync',
                                   'replace_before', 'replace_after', 'directory_fsync'])
def test_torn_advancement_never_retries_or_erases_old_state(case, monkeypatch, fault):
    root, _, host, volume, ticket, digest, _ = case
    old = root / ('.generation-' + ticket)
    preserved = {p: p.read_bytes() for p in old.iterdir()}
    writer, opener, syncer, replacer = h.io.exclusive_write, os.open, os.fsync, os.replace
    stage_fds = set()
    replaced = False
    def write(path, data):
        if ((fault == 'prior_link' and path.name == 'predecessor-link.json')
                or (fault == 'reservation' and path.name == 'reservation.json')):
            raise OSError('synthetic successor publication fault')
        writer(path, data)
    def open_file(path, flags, *args, **kwargs):
        if isinstance(path, str) and path.startswith('.generation-next-'):
            if fault == 'stage_write':
                raise OSError('synthetic successor publication fault')
            fd = opener(path, flags, *args, **kwargs)
            stage_fds.add(fd)
            return fd
        return opener(path, flags, *args, **kwargs)
    def fsync(fd):
        if ((fault == 'stage_fsync' and fd in stage_fds)
                or (fault == 'directory_fsync' and replaced)):
            raise OSError('synthetic successor publication fault')
        syncer(fd)
    def replace(src, dst, **kwargs):
        nonlocal replaced
        assert kwargs['src_dir_fd'] == kwargs['dst_dir_fd']
        if fault == 'replace_before':
            raise OSError('synthetic successor publication fault')
        replacer(src, dst, **kwargs)
        replaced = True
        if fault == 'replace_after':
            raise OSError('synthetic successor publication fault')
    monkeypatch.setattr(h.io, 'exclusive_write', write)
    monkeypatch.setattr(os, 'open', open_file)
    monkeypatch.setattr(os, 'fsync', fsync)
    monkeypatch.setattr(os, 'replace', replace)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        with pytest.raises(OSError, match='synthetic successor publication fault'):
            g.reserve(root, digest, host, volume)
        with pytest.raises(RuntimeError, match='prior_generation_requires_inspection'):
            g.reserve(root, digest, host, volume)
    assert (root / 'server.json').read_bytes() == _final(0)
    assert all(p.read_bytes() == raw for p, raw in preserved.items())


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('drift', ['root', 'lock'])
def test_link_replace_is_bound_to_open_directory_and_rechecks_namespace(case, monkeypatch, drift):
    root, _, host, volume, _, digest, _ = case
    replace = os.replace
    moved = root.with_name('old-runtime')
    def changed_namespace(src, dst, **kwargs):
        assert kwargs['src_dir_fd'] == kwargs['dst_dir_fd']
        if drift == 'root':
            root.rename(moved)
            root.mkdir()
            _write(root / '.generation-current.json', b'untouched foreign namespace')
        else:
            (root / 'boot.lock').unlink()
            (root / 'boot.lock').touch()
        replace(src, dst, **kwargs)
    monkeypatch.setattr(os, 'replace', changed_namespace)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        with pytest.raises(RuntimeError):
            g.reserve(root, digest, host, volume)
    if drift == 'root':
        assert (root / '.generation-current.json').read_bytes() == b'untouched foreign namespace'
        assert (moved / 'server.json').read_bytes() == _final(0)
    else:
        assert (root / 'server.json').read_bytes() == _final(0)


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('change', ['missing_link', 'torn_link', 'ancestor_extra', 'cycle'])
def test_authenticated_ancestor_damage_refuses_later_publication(case, monkeypatch, change):
    root, _, host, volume, first, digest, _ = case
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        second = g.reserve(root, digest, host, volume)
        child = root / ('.generation-' + second)
        if change == 'missing_link':
            (child / 'predecessor-link.json').unlink()
        elif change == 'torn_link':
            _write(child / 'predecessor-link.json', b'{')
        elif change == 'ancestor_extra':
            (root / '.generation-unlinked').mkdir()
        else:
            _, value = h.io.read_json(child / 'reservation.json')
            value['prior_generation']['ticket'] = second
            _write(child / 'reservation.json', h.io.encoded(value))
        with pytest.raises((RuntimeError, ValueError, OSError)):
            g.authorize(root, second, host, volume)
        assert not (child / 'authorized.json').exists()
        assert (root / ('.generation-' + first) / 'publication-2.json').exists()
