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
def case(tmp_path, monkeypatch, request):
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
    if checkpoint_boot := getattr(request, 'param', None):
        facts['boot'] = checkpoint_boot
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


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('case', [None, '55555555-5555-4555-8555-555555555555'], indirect=True)
@pytest.mark.parametrize('reserve_later', [False, True])
def test_checkpoint_boot_is_independent_of_launch_and_successor_boot(case, reserve_later):
    root, facts, host, volume, first, digest, retired = case
    if reserve_later:
        facts['boot'] = '66666666-6666-4666-8666-666666666666'
    parent_dir = root / ('.generation-' + first)
    preserved = {path: path.read_bytes() for path in parent_dir.iterdir()}
    checkpoint_path = root / ('.router-checkpoint-' + digest + '.json')
    checkpoint_bytes = checkpoint_path.read_bytes()
    receipt_bytes = Path(retired['receipt_path']).read_bytes()

    second = _advance(root, host, volume, 1)

    assert second != first
    assert (root / 'server.json').read_bytes() == _final(1)
    assert checkpoint_path.read_bytes() == checkpoint_bytes
    assert Path(retired['receipt_path']).read_bytes() == receipt_bytes
    assert Path(retired['recovery_path']).read_bytes() == _final(0)
    assert all(path.read_bytes() == raw for path, raw in preserved.items())


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('case', ['55555555-5555-4555-8555-555555555555'], indirect=True)
@pytest.mark.parametrize('fault', ['checkpoint_host', 'receipt_prior', 'retirement_host',
                                  'retirement_same_boot', 'successor_host',
                                  'successor_checkpoint_boot', 'successor_launch_boot'])
def test_historical_identity_relations_refuse_even_with_consistent_hashes(case, fault):
    root, facts, _, _, first, digest, retired = case
    parent_dir = root / ('.generation-' + first)
    _, parent = h.io.read_json(parent_dir / 'reservation.json')
    checkpoint_path = root / ('.router-checkpoint-' + digest + '.json')
    _, checkpoint = h.io.read_json(checkpoint_path)
    receipt_path = Path(retired['receipt_path'])
    _, receipt = h.io.read_json(receipt_path)
    child = {'identity': dict(facts)}
    foreign = '77777777-7777-4777-8777-777777777777'
    def foreign_checkpoint():
        checkpoint['identity']['host'] = foreign
        receipt['prior_identity'] = dict(checkpoint['identity'])
        receipt['retired_identity']['host'] = foreign
    changes = {
        'checkpoint_host': foreign_checkpoint,
        'receipt_prior': lambda: receipt.update(prior_identity=dict(parent['identity'])),
        'retirement_host': lambda: receipt['retired_identity'].update(host=foreign),
        'retirement_same_boot': lambda: receipt['retired_identity'].update(boot=checkpoint['identity']['boot']),
        'successor_host': lambda: child['identity'].update(host=foreign),
        'successor_checkpoint_boot': lambda: child['identity'].update(boot=checkpoint['identity']['boot']),
        'successor_launch_boot': lambda: child['identity'].update(boot=parent['identity']['boot']),
    }
    changes[fault]()
    checkpoint_raw = h.io.encoded(checkpoint)
    _write(checkpoint_path, checkpoint_raw)
    receipt['checkpoint_sha256'] = hashlib.sha256(checkpoint_raw).hexdigest()
    receipt_raw = h.io.encoded(receipt)
    _write(receipt_path, receipt_raw)
    reference = {'retirement_sha256': hashlib.sha256(receipt_raw).hexdigest()}
    link = (root / '.generation-current.json').read_bytes()
    state = (root / 'server.json').read_bytes()
    preserved = {path: path.read_bytes() for path in parent_dir.iterdir()}

    with pytest.raises(RuntimeError, match='generation_retirement_unqualified'):
        h._retired_ancestor(root, digest, reference, parent, child)

    assert (root / '.generation-current.json').read_bytes() == link
    assert (root / 'server.json').read_bytes() == state
    assert Path(retired['recovery_path']).read_bytes() == _final(0)
    assert all(path.read_bytes() == raw for path, raw in preserved.items())


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('case', ['55555555-5555-4555-8555-555555555555'], indirect=True)
@pytest.mark.parametrize('fault', ['launch_boot', 'checkpoint_boot', 'foreign_host'])
def test_invalid_successor_identity_refuses_before_link_replacement(case, fault):
    root, facts, host, volume, first, digest, retired = case
    changes = {
        'launch_boot': {'boot': '22222222-2222-4222-8222-222222222222'},
        'checkpoint_boot': {'boot': '55555555-5555-4555-8555-555555555555'},
        'foreign_host': {'host': '77777777-7777-4777-8777-777777777777'},
    }
    facts.update(changes[fault])
    link = (root / '.generation-current.json').read_bytes()
    parent_dir = root / ('.generation-' + first)
    preserved = {path: path.read_bytes() for path in parent_dir.iterdir()}
    before = set(root.iterdir())

    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        with pytest.raises(RuntimeError, match='prior_generation_requires_inspection'):
            g.reserve(root, digest, host, volume)

    assert set(root.iterdir()) == before
    assert (root / '.generation-current.json').read_bytes() == link
    assert (root / 'server.json').read_bytes() == _final(0)
    assert Path(retired['recovery_path']).read_bytes() == _final(0)
    assert all(path.read_bytes() == raw for path, raw in preserved.items())
