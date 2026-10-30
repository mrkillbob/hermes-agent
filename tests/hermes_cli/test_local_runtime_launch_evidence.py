"""An interrupted pre-spawn intent needs recoverable prospective evidence."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli.local_runtime import binaries, bootstrap, reconciliation, generation_evidence, supervisor


def _replace_private(path, contents):
    path.write_text(contents)
    path.chmod(0o600)


@pytest.mark.platforms('macos')
def test_legacy_incomplete_intent_cannot_be_retroactively_checkpointed(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    root = home / 'runtimes' / 'llamacpp'
    root.mkdir(parents=True)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(reconciliation, 'runtimes_root', lambda: root)
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    monkeypatch.setattr(reconciliation, '_native_identity', lambda: {
        'host': '11111111-1111-4111-8111-111111111111',
        'boot': '22222222-2222-4222-8222-222222222222'})
    monkeypatch.setattr(reconciliation, '_volume_identity', lambda fd: '33333333-3333-4333-8333-333333333333')
    path = root / 'server.json'
    raw = json.dumps({'tree_custody': 'unverified', 'spawn_ticket': 'a' * 32, 'owner_pid': 123}).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    with pytest.raises(RuntimeError, match='modern_record_required'):
        reconciliation.checkpoint(expected_sha256=hashlib.sha256(raw).hexdigest(), timeout_s=0)
    assert path.read_bytes() == raw


@pytest.mark.platforms('macos')
def test_generation_is_durable_and_requires_real_lock(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    host = lambda: {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    with pytest.raises(RuntimeError, match='runtime_lock_required'):
        generation_evidence.reserve(root, None, host, volume)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, host, volume)
        assert (root / '.generation-current.json').exists()
        assert not (root / ('.generation-' + ticket) / 'authorized.json').exists()
        generation_evidence.authorize(root, ticket, host, volume)
        assert (root / ('.generation-' + ticket) / 'authorized.json').exists()


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('change', ['boot', 'predecessor', 'extra_directory', 'torn_link', 'source'])
def test_changed_or_ambiguous_generation_never_authorizes(tmp_path, monkeypatch, change):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    facts = {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, host, volume)
        changes = {
            'boot': lambda: facts.update(boot='44444444-4444-4444-8444-444444444444'),
            'predecessor': lambda: _replace_private(root / 'server.json', '{}'),
            'extra_directory': lambda: (root / '.generation-other').mkdir(),
            'torn_link': lambda: (root / '.generation-current.json').write_text('{'),
            'source': lambda: monkeypatch.setattr(generation_evidence, '_source_identity',
                                                  lambda: {'changed': True}),
        }
        changes[change]()
        with pytest.raises((RuntimeError, ValueError)):
            generation_evidence.authorize(root, ticket, host, volume)
        assert not (root / ('.generation-' + ticket) / 'authorized.json').exists()


@pytest.mark.platforms('macos')
def test_torn_link_publication_retains_ambiguous_fence(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    host = lambda: {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    writer = generation_evidence.io.exclusive_write
    def fail_link(path, data):
        if path.name == '.generation-current.json':
            raise OSError('synthetic publication fault')
        writer(path, data)
    monkeypatch.setattr(generation_evidence.io, 'exclusive_write', fail_link)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        with pytest.raises(OSError, match='synthetic publication fault'):
            generation_evidence.reserve(root, None, host, volume)
        with pytest.raises(RuntimeError, match='prior_generation_requires_inspection'):
            generation_evidence.reserve(root, None, host, volume)
        assert len(list(root.glob('.generation-*'))) == 1


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('change', ['predecessor', 'link', 'lock'])
def test_drift_during_authorization_is_not_launch_authority(tmp_path, monkeypatch, change):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    host = lambda: {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    writer = generation_evidence.io.exclusive_write
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, host, volume)
        def drift(path, data):
            writer(path, data)
            if path.name == 'authorized.json':
                if change == 'predecessor':
                    (root / 'server.json').write_text('{}')
                    (root / 'server.json').chmod(0o600)
                elif change == 'link':
                    (root / '.generation-current.json').write_text('{}')
                else:
                    (root / 'boot.lock').unlink()
                    (root / 'boot.lock').touch()
        monkeypatch.setattr(generation_evidence.io, 'exclusive_write', drift)
        with pytest.raises(RuntimeError):
            generation_evidence.authorize(root, ticket, host, volume)
        assert (root / ('.generation-' + ticket) / 'authorized.json').exists()


@pytest.mark.platforms('macos')
def test_interrupted_generation_requires_new_kernel_and_retains_evidence(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    facts = {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, host, volume)
        generation_evidence.authorize(root, ticket, host, volume)
        with pytest.raises(RuntimeError, match='kernel_boundary_required'):
            generation_evidence.verify_interrupted(root, ticket, host, volume)
        facts['boot'] = '44444444-4444-4444-8444-444444444444'
        result = generation_evidence.verify_interrupted(root, ticket, host, volume)
        assert result == {'outcome': 'verified_only', 'ticket': ticket, 'retired': False}
        assert (root / '.generation-current.json').exists()
        assert (root / ('.generation-' + ticket) / 'authorized.json').exists()
        assert not (root / 'server.json').exists()


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('change', ['host', 'volume', 'source', 'missing_authorization', 'replacement'])
def test_interrupted_verifier_refuses_unbound_or_changed_evidence(tmp_path, monkeypatch, change):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    facts = {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volumes = ['33333333-3333-4333-8333-333333333333']
    volume = lambda fd: volumes[0]
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, host, volume)
        generation_evidence.authorize(root, ticket, host, volume)
        facts['boot'] = '44444444-4444-4444-8444-444444444444'
        changes = {
            'host': lambda: facts.update(host='55555555-5555-4555-8555-555555555555'),
            'volume': lambda: volumes.__setitem__(0, '66666666-6666-4666-8666-666666666666'),
            'source': lambda: monkeypatch.setattr(generation_evidence, '_source_identity',
                                                  lambda: {'changed': True}),
            'missing_authorization': lambda: (root / ('.generation-' + ticket) / 'authorized.json').unlink(),
            'replacement': lambda: _replace_private(root / 'server.json', 'torn replacement'),
        }
        changes[change]()
        with pytest.raises((RuntimeError, OSError)):
            generation_evidence.verify_interrupted(root, ticket, host, volume)
        assert (root / '.generation-current.json').exists()


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('stage', ['reserve', 'authorize', 'verify'])
def test_unreadable_predecessor_never_counts_as_absent(tmp_path, monkeypatch, stage):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    facts = {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = None
        if stage != 'reserve':
            ticket = generation_evidence.reserve(root, None, host, volume)
        if stage == 'verify':
            generation_evidence.authorize(root, ticket, host, volume)
            facts['boot'] = '44444444-4444-4444-8444-444444444444'
        lstat = Path.lstat
        def unreadable(path):
            if path == root / 'server.json':
                raise PermissionError('synthetic unavailable state')
            return lstat(path)
        monkeypatch.setattr(Path, 'lstat', unreadable)
        with pytest.raises(PermissionError, match='synthetic unavailable state'):
            if stage == 'reserve':
                generation_evidence.reserve(root, None, host, volume)
            elif stage == 'authorize':
                generation_evidence.authorize(root, ticket, host, volume)
            else:
                generation_evidence.verify_interrupted(root, ticket, host, volume)


@pytest.mark.platforms('macos')
def test_published_state_is_bound_before_replacement_and_survives_kernel_boundary(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    facts = {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, host, volume)
        generation_evidence.authorize(root, ticket, host, volume)
        intended = json.dumps({'tree_custody': 'unverified', 'spawn_ticket': ticket}).encode()
        generation_evidence.bind_publication(root, ticket, intended, host, volume)
        path = root / 'server.json'
        path.write_bytes(intended)
        path.chmod(0o600)
        facts['boot'] = '44444444-4444-4444-8444-444444444444'
        assert generation_evidence.verify_interrupted(root, ticket, host, volume)['outcome'] == 'verified_only'
        path.write_bytes(intended[:-1])
        with pytest.raises(RuntimeError, match='generation_evidence_changed'):
            generation_evidence.verify_interrupted(root, ticket, host, volume)


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('contents', ['{}', '{'])
def test_publication_appearing_during_verification_refuses(tmp_path, monkeypatch, contents):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    facts = {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, host, volume)
        generation_evidence.authorize(root, ticket, host, volume)
        facts['boot'] = '44444444-4444-4444-8444-444444444444'
        def publish_during_read():
            path = root / ('.generation-' + ticket) / 'publication.json'
            path.write_text(contents)
            path.chmod(0o600)
            return dict(facts)
        with pytest.raises(RuntimeError, match='generation_evidence_changed'):
            generation_evidence.verify_interrupted(root, ticket, publish_during_read, volume)


@pytest.mark.platforms('macos')
def test_two_durable_publications_are_linked_to_one_generation(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    facts = {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, host, volume)
        generation_evidence.authorize(root, ticket, host, volume)
        path = root / 'server.json'
        intent = json.dumps({'tree_custody': 'unverified', 'spawn_ticket': ticket}).encode()
        generation_evidence.bind_publication(root, ticket, intent, host, volume)
        path.write_bytes(intent)
        path.chmod(0o600)
        complete = json.dumps({'tree_custody': 'unverified', 'spawn_ticket': ticket, 'pid': 42}).encode()
        generation_evidence.bind_publication(root, ticket, complete, host, volume)
        path.write_bytes(complete)
        facts['boot'] = '44444444-4444-4444-8444-444444444444'
        assert generation_evidence.verify_interrupted(root, ticket, host, volume)['outcome'] == 'verified_only'


@pytest.mark.platforms('macos')
def test_publication_refuses_noop_before_writing_receipt(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    state = b'{}'
    (root / 'server.json').write_bytes(state)
    (root / 'server.json').chmod(0o600)
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    host = lambda: {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, hashlib.sha256(state).hexdigest(), host, volume)
        generation_evidence.authorize(root, ticket, host, volume)
        with pytest.raises(RuntimeError, match='publication_state_unchanged'):
            generation_evidence.bind_publication(root, ticket, state, host, volume)
        assert not list((root / ('.generation-' + ticket)).glob('publication*'))


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('change', ['first_missing', 'first_torn', 'first_link', 'second_predecessor',
                                    'extra', 'second_missing'])
def test_publication_chain_refuses_missing_or_rewritten_steps(tmp_path, monkeypatch, change):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    facts = {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, host, volume)
        generation_evidence.authorize(root, ticket, host, volume)
        directory = root / ('.generation-' + ticket)
        path = root / 'server.json'
        first = b'{"stage":"intent"}'
        generation_evidence.bind_publication(root, ticket, first, host, volume)
        path.write_bytes(first)
        path.chmod(0o600)
        second = b'{"stage":"published"}'
        generation_evidence.bind_publication(root, ticket, second, host, volume)
        path.write_bytes(second)
        def rewrite_second(field):
            name = directory / 'publication-2.json'
            value = json.loads(name.read_text())
            value[field] = '0' * 64
            name.write_text(json.dumps(value))
        changes = {
            'first_missing': lambda: (directory / 'publication.json').unlink(),
            'first_torn': lambda: (directory / 'publication.json').write_text('{'),
            'first_link': lambda: rewrite_second('previous_publication_sha256'),
            'second_predecessor': lambda: rewrite_second('predecessor_sha256'),
            'extra': lambda: (directory / 'publication-3.json').write_text('{}'),
            'second_missing': lambda: (directory / 'publication-2.json').unlink(),
        }
        changes[change]()
        facts['boot'] = '44444444-4444-4444-8444-444444444444'
        with pytest.raises((RuntimeError, ValueError)):
            generation_evidence.verify_interrupted(root, ticket, host, volume)


@pytest.mark.platforms('macos')
def test_second_publication_appearing_during_verification_refuses(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    facts = {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, host, volume)
        generation_evidence.authorize(root, ticket, host, volume)
        directory = root / ('.generation-' + ticket)
        first = b'{"stage":"intent"}'
        generation_evidence.bind_publication(root, ticket, first, host, volume)
        (root / 'server.json').write_bytes(first)
        (root / 'server.json').chmod(0o600)
        facts['boot'] = '44444444-4444-4444-8444-444444444444'
        calls = 0
        def appear():
            nonlocal calls
            calls += 1
            if calls == 2:
                (directory / 'publication-2.json').write_text('{}')
                (directory / 'publication-2.json').chmod(0o600)
            return dict(facts)
        with pytest.raises(RuntimeError, match='generation_evidence_changed'):
            generation_evidence.verify_interrupted(root, ticket, appear, volume)


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('final_drift', ['none', 'lock', 'root'])
def test_owned_engine_probe_reserves_before_child_and_binds_both_state_writes(tmp_path, monkeypatch, final_drift):
    root = tmp_path / 'runtime'
    root.mkdir()
    facts = {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    for module in (bootstrap, binaries, supervisor, reconciliation):
        monkeypatch.setattr(module, 'runtimes_root', lambda: root)
    monkeypatch.setattr(reconciliation, '_native_identity', host)
    monkeypatch.setattr(reconciliation, '_volume_identity', volume)
    monkeypatch.setattr(bootstrap, '_BOOT_OWNER', object())
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    def probe():
        link = json.loads((root / '.generation-current.json').read_text())
        assert (root / ('.generation-' + link['ticket']) / 'authorized.json').exists()
        return 'amd'
    monkeypatch.setattr(bootstrap, '_detect_gpu_vendor', probe)
    monkeypatch.setattr(binaries.pm, 'current_target', lambda: 'fixture-target')
    monkeypatch.setattr(binaries, '_candidates', lambda *a: ('cpu',))
    monkeypatch.setattr(binaries, '_installed', lambda *a: binaries.Engine('cpu', 'fixture', root / 'engine'))
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        assert binaries.installed_engine('auto').tag == 'fixture'
        sup = supervisor.LlamaServerSupervisor(root / 'engine', root / 'models', port=59996)
        assert sup._generation_ticket
        def help_probe(exe):
            assert json.loads((root / 'server.json').read_text())['tree_custody'] == 'unverified'
            raise OSError('synthetic stop before child')
        monkeypatch.setattr(supervisor, '_direct_io_args', help_probe)
        with pytest.raises(OSError, match='synthetic stop before child'):
            sup._spawn()
        assert (root / ('.generation-' + sup._generation_ticket) / 'publication.json').exists()
        assert not (root / ('.generation-' + sup._generation_ticket) / 'publication-2.json').exists()
        sup.proc = SimpleNamespace(pid=42)
        class Process:
            def __init__(self, pid=None):
                self.pid = pid or os.getpid()
            def create_time(self):
                return 42.0
            def exe(self):
                return str(root / 'engine')
        monkeypatch.setattr('psutil.Process', Process)
        monkeypatch.setattr('gateway.status.get_process_start_time', lambda pid: 42.0)
        if final_drift != 'none':
            import utils
            writer = utils.atomic_json_write
            def drifting_write(*args, **kwargs):
                writer(*args, **kwargs)
                if final_drift == 'lock':
                    (root / 'boot.lock').unlink()
                    (root / 'boot.lock').touch()
                else:
                    root.rename(tmp_path / 'prior-runtime')
                    root.mkdir()
            monkeypatch.setattr(utils, 'atomic_json_write', drifting_write)
            with pytest.raises((RuntimeError, OSError)):
                sup._write_state()
            return
        sup._write_state()
        assert (root / ('.generation-' + sup._generation_ticket) / 'publication-2.json').exists()
        facts['boot'] = '44444444-4444-4444-8444-444444444444'
        assert generation_evidence.verify_interrupted(root, sup._generation_ticket, host, volume)['retired'] is False


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('backend', ['metal', 'cpu', 'auto'])
def test_missing_engine_leaves_no_generation_and_later_install_can_boot(tmp_path, monkeypatch, backend):
    root = tmp_path / 'runtime'
    root.mkdir()
    for module in (bootstrap, binaries):
        monkeypatch.setattr(module, 'runtimes_root', lambda: root)
    monkeypatch.setattr(bootstrap, '_BOOT_OWNER', object())
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    monkeypatch.setattr(binaries.pm, 'current_target', lambda: 'darwin-aarch64')
    monkeypatch.setattr(binaries, '_installed', lambda *a: None)
    monkeypatch.setattr(binaries, '_legacy_installs', lambda *a: [])
    monkeypatch.setattr(bootstrap, '_detect_gpu_vendor', lambda: pytest.fail('absent engine reached native probe'))
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        assert binaries.installed_engine(backend) is None
        assert not list(root.glob('.generation-*'))
        monkeypatch.setattr(binaries, '_installed', lambda *a: binaries.Engine('cpu', 'fixture', root / 'engine'))
        monkeypatch.setattr(bootstrap, '_detect_gpu_vendor', lambda: None)
        monkeypatch.setattr(reconciliation, '_native_identity', lambda: {
            'host': '11111111-1111-4111-8111-111111111111',
            'boot': '22222222-2222-4222-8222-222222222222'})
        monkeypatch.setattr(reconciliation, '_volume_identity', lambda fd: '33333333-3333-4333-8333-333333333333')
        assert binaries.installed_engine(backend).tag == 'fixture'
        assert generation_evidence.current_boot_ticket(root)
        assert (root / '.generation-current.json').exists()


@pytest.mark.platforms('macos')
def test_legacy_candidate_still_reserves_before_probe(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    for module in (bootstrap, binaries):
        monkeypatch.setattr(module, 'runtimes_root', lambda: root)
    monkeypatch.setattr(bootstrap, '_BOOT_OWNER', object())
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    monkeypatch.setattr(binaries, '_installed', lambda *a: None)
    monkeypatch.setattr(binaries, '_legacy_installs', lambda *a: [('fixture', root, {})])
    monkeypatch.setattr(reconciliation, '_native_identity', lambda: {
        'host': '11111111-1111-4111-8111-111111111111',
        'boot': '22222222-2222-4222-8222-222222222222'})
    monkeypatch.setattr(reconciliation, '_volume_identity', lambda fd: '33333333-3333-4333-8333-333333333333')
    def probe():
        assert (root / '.generation-current.json').exists()
        raise OSError('synthetic interrupted probe')
    monkeypatch.setattr(bootstrap, '_detect_gpu_vendor', probe)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        with pytest.raises(OSError, match='synthetic interrupted probe'):
            binaries.installed_engine('auto')
        assert (root / '.generation-current.json').exists()


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('copy_context', [False, True])
def test_other_thread_engine_lookup_during_boot_does_not_reserve(tmp_path, monkeypatch, copy_context):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context as snapshot_context

    root = tmp_path / 'runtime'
    root.mkdir()
    for module in (bootstrap, binaries):
        monkeypatch.setattr(module, 'runtimes_root', lambda: root)
    monkeypatch.setattr(bootstrap, '_BOOT_OWNER', object())
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    monkeypatch.setattr(binaries.pm, 'current_target', lambda: 'fixture-target')
    monkeypatch.setattr(binaries, '_candidates', lambda *a: ('cpu',))
    monkeypatch.setattr(binaries, '_installed', lambda *a: binaries.Engine('cpu', 'fixture', root / 'engine'))
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        def lookup():
            assert generation_evidence.current_boot_ticket(root) is None
            return binaries.installed_engine('cpu').tag
        context = snapshot_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(context.run, lookup) if copy_context else pool.submit(lookup)
            assert future.result() == 'fixture'
        assert not list(root.glob('.generation-*'))


@pytest.mark.platforms('macos')
def test_owned_boot_context_with_replaced_lock_still_refuses(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(binaries, 'runtimes_root', lambda: root)
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    monkeypatch.setattr(bootstrap, '_BOOT_OWNER', object())
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    monkeypatch.setattr(binaries, '_installed', lambda *a: binaries.Engine('cpu', 'fixture', root / 'engine'))
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        (root / 'boot.lock').unlink()
        (root / 'boot.lock').touch()
        with pytest.raises(RuntimeError, match='runtime_lock_required'):
            binaries.installed_engine('cpu')
        assert not list(root.glob('.generation-*'))


@pytest.mark.platforms('macos')
def test_engine_probe_without_boot_owner_does_not_create_generation(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(binaries, 'runtimes_root', lambda: root)
    monkeypatch.setattr(bootstrap, '_BOOT_OWNER', None)
    monkeypatch.setattr(binaries.pm, 'current_target', lambda: 'fixture-target')
    monkeypatch.setattr(binaries, '_candidates', lambda *a: ('cpu',))
    monkeypatch.setattr(binaries, '_installed', lambda *a: binaries.Engine('cpu', 'fixture', root / 'engine'))
    assert binaries.installed_engine('cpu').tag == 'fixture'
    assert not list(root.glob('.generation-*'))


@pytest.mark.platforms('macos')
def test_accepted_checkpoint_is_consumed_before_generation_replaces_record(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    facts = {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    for module in (bootstrap, binaries, reconciliation):
        monkeypatch.setattr(module, 'runtimes_root', lambda: root)
    monkeypatch.setattr(reconciliation, '_native_identity', host)
    monkeypatch.setattr(reconciliation, '_volume_identity', volume)
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', None)
    original = json.dumps(dict(pid=42, owner_pid=41, create_time=20.0,
                               owner_create_time=19.0, executable='/synthetic/engine')).encode()
    path = root / 'server.json'
    path.write_bytes(original)
    path.chmod(0o600)
    digest = hashlib.sha256(original).hexdigest()
    checkpoint = reconciliation.checkpoint(expected_sha256=digest)
    facts['boot'] = '44444444-4444-4444-8444-444444444444'
    result = reconciliation.reconcile(expected_sha256=digest, checkpoint_id=checkpoint['checkpoint_id'])
    assert result['outcome'] == 'retired_in_place'
    assert path.read_bytes() == original
    monkeypatch.setattr(bootstrap, '_BOOT_OWNER', object())
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        generation_evidence.reserve_for_engine_probe()
        ticket = generation_evidence.current_boot_ticket(root)
        intent = generation_evidence.publish_intent(root, ticket, host, volume)
        assert json.loads(path.read_text()) == intent
        assert (root / ('.router-recovery-' + checkpoint['checkpoint_id']) / 'server.json').read_bytes() == original
        assert (root / ('.generation-' + ticket) / 'publication.json').exists()


@pytest.mark.platforms('macos')
def test_first_state_failure_cannot_leave_torn_canonical_intent(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    facts = {'host': '11111111-1111-4111-8111-111111111111', 'boot': '22222222-2222-4222-8222-222222222222'}
    host = lambda: dict(facts)
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, host, volume)
        generation_evidence.authorize(root, ticket, host, volume)
        original = generation_evidence.ctypes.CDLL
        def failed_rename(*args, **kwargs):
            api = original(*args, **kwargs)
            class Refused:
                renameatx_np = staticmethod(lambda *a: -1)
            return Refused() if args and args[0] is None else api
        monkeypatch.setattr(generation_evidence.ctypes, 'CDLL', failed_rename)
        with pytest.raises(OSError, match='publication refused'):
            generation_evidence.publish_intent(root, ticket, host, volume)
        assert not (root / 'server.json').exists()
        assert (root / ('.generation-stage-' + ticket)).exists()
        facts['boot'] = '44444444-4444-4444-8444-444444444444'
        with pytest.raises(RuntimeError, match='generation_link_ambiguous'):
            generation_evidence.verify_interrupted(root, ticket, host, volume)


@pytest.mark.platforms('macos')
def test_no_clobber_state_publication_preserves_competing_record(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    existing = b'{"foreign":true}'
    (root / 'server.json').write_bytes(existing)
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        with pytest.raises(OSError):
            volume = lambda fd: '33333333-3333-4333-8333-333333333333'
            generation_evidence._exclusive_atomic_state(
                root, 'a' * 32, b'{"ours":true}', volume, generation_evidence._root(root, volume))
    assert (root / 'server.json').read_bytes() == existing


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('drift_call', [2, 4])
def test_staging_descriptor_volume_drift_refuses_publication(tmp_path, monkeypatch, drift_call):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    expected = '33333333-3333-4333-8333-333333333333'
    binding = generation_evidence._root(root, lambda fd: expected)
    calls = 0
    def volume(fd):
        nonlocal calls
        calls += 1
        return '44444444-4444-4444-8444-444444444444' if calls == drift_call else expected
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        with pytest.raises(RuntimeError, match='runtime_directory_changed'):
            generation_evidence._exclusive_atomic_state(root, 'a' * 32, b'{}', volume, binding)
    assert not (root / 'server.json').exists()


@pytest.mark.platforms('macos')
def test_direct_supervisor_cannot_cross_another_generation_fence(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(supervisor, 'runtimes_root', lambda: root)
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    monkeypatch.setattr(reconciliation, '_native_identity', lambda: {
        'host': '11111111-1111-4111-8111-111111111111',
        'boot': '22222222-2222-4222-8222-222222222222'})
    monkeypatch.setattr(reconciliation, '_volume_identity',
                        lambda fd: '33333333-3333-4333-8333-333333333333')
    spawned = []
    monkeypatch.setattr(supervisor, 'spawn_server', lambda *a, **k: spawned.append(True))
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, reconciliation._native_identity,
                                             reconciliation._volume_identity)
        generation_evidence.authorize(root, ticket, reconciliation._native_identity,
                                      reconciliation._volume_identity)
        direct = supervisor.LlamaServerSupervisor(root / 'engine', root / 'models', port=59996)
        with pytest.raises(RuntimeError, match='prior_generation_requires_inspection'):
            direct._spawn()
    assert not spawned
    assert not (root / 'server.json').exists()


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('change', ['lock_after', 'root_before', 'root_after'])
def test_intent_publication_refuses_lost_lock_or_root_before_next_probe(tmp_path, monkeypatch, change):
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setattr(bootstrap, 'runtimes_root', lambda: root)
    host = lambda: {'host': '11111111-1111-4111-8111-111111111111',
                    'boot': '22222222-2222-4222-8222-222222222222'}
    volume = lambda fd: '33333333-3333-4333-8333-333333333333'
    writer = generation_evidence._exclusive_atomic_state
    with bootstrap._cross_process_boot_lock(timeout_s=0) as acquired:
        assert acquired
        ticket = generation_evidence.reserve(root, None, host, volume)
        generation_evidence.authorize(root, ticket, host, volume)
        def swap_root():
            root.rename(tmp_path / 'prior-runtime')
            root.mkdir()
        def drift(*args):
            if change == 'root_before':
                swap_root()
            else:
                writer(*args)
            if change == 'lock_after':
                (root / 'boot.lock').unlink()
                (root / 'boot.lock').touch()
            elif change == 'root_after':
                swap_root()
            if change == 'root_before':
                writer(*args)
        monkeypatch.setattr(generation_evidence, '_exclusive_atomic_state', drift)
        with pytest.raises((RuntimeError, OSError)):
            generation_evidence.publish_intent(root, ticket, host, volume)
    if change == 'lock_after':
        assert (root / 'server.json').exists()
    else:
        assert not (root / 'server.json').exists()
