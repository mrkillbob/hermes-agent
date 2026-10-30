"""Opt-in local-volume lifetime protocol across Darwin device renumbering."""
import json

import pytest

pytestmark = pytest.mark.platforms('macos')


def test_opted_in_lifetime_persists_explicit_volume_protocol(tmp_path):
    from pm import lifetime
    domain = tmp_path / 'state'
    with lifetime.activity(domain, create=True, stable_identity=True):
        with lifetime.gate(domain) as guard:
            assert guard.qualified
            assert guard.held()
            record = json.loads(guard.marker.read_bytes())
            assert record['version'] == 2
            assert record['identity'] == 'darwin_local_volume_uuid'
    with lifetime.activity(domain):
        with lifetime.gate(domain) as guard:
            assert guard.qualified and guard.held()
    with lifetime.gate(domain) as guard:
        assert not guard.held()


def test_failed_output_cleanup_uses_the_owner_protocol(tmp_path):
    from pm import lifetime
    from pm.lifetime_cleanup import defer_failed_output, output_identity
    domain = tmp_path / 'state'
    with lifetime.activity(domain, create=True, stable_identity=True):
        output = domain / 'failed'
        output.mkdir()
        assert defer_failed_output(output, output_identity(output))
    assert not output.exists()


def test_float_inode_marker_never_becomes_a_native_identity(tmp_path):
    from pm import lifetime
    domain = tmp_path / 'state'
    with lifetime.activity(domain, create=True, stable_identity=True):
        pass
    marker = lifetime.Gate(domain).marker
    record = json.loads(marker.read_bytes())
    record['domain'][1] = float(record['domain'][1])
    marker.write_text(json.dumps(record))
    before = marker.read_bytes()
    with pytest.raises(lifetime.LifetimeUnavailable):
        with lifetime.activity(domain):
            pass
    assert marker.read_bytes() == before


@pytest.mark.parametrize('fault', ['float', 'v1', 'kind', 'version'])
def test_malformed_activity_identity_is_retained_as_unknown(tmp_path, fault):
    from pm import lifetime
    domain = tmp_path / 'state'
    with lifetime.activity(domain, create=True, stable_identity=True):
        pass
    with lifetime.gate(domain) as guard:
        identity = guard._snapshot()['domain']
        if fault == 'float':
            identity[1] = float(identity[1])
        ref = guard.activities / ('a' * 32)
        record = dict(version=2, identity='darwin_local_volume_uuid',
                      domain=identity, platform='posix', generation=None)
        if fault == 'v1':
            record['version'] = 1
            del record['identity']
        elif fault == 'kind':
            record['identity'] = 'unknown'
        elif fault == 'version':
            record['version'] = 3
        ref.write_text(json.dumps(record))
        before = ref.read_bytes()
        assert guard.held()
        assert ref.read_bytes() == before


@pytest.mark.parametrize('old', ['qualified-v1', 'unqualified', 'namespace-only', 'retired-v1'])
def test_opt_in_never_rebinds_existing_or_ambiguous_state(tmp_path, old):
    from pm import lifetime
    domain = tmp_path / 'state'
    if old in {'qualified-v1', 'retired-v1'}:
        with lifetime.activity(domain, create=True):
            pass
        if old == 'retired-v1':
            with lifetime.gate(domain) as guard:
                ownership = guard._snapshot()
                domain.rmdir()
                guard.confirm_removed(ownership)
    elif old == 'unqualified':
        domain.mkdir()
    else:
        with lifetime.gate(domain):
            pass
    marker = lifetime.Gate(domain).marker
    before = marker.read_bytes() if marker.exists() else None
    with pytest.raises(lifetime.LifetimeUnavailable, match='requires new qualified state'):
        with lifetime.activity(domain, create=True, stable_identity=True):
            pass
    assert (marker.read_bytes() if marker.exists() else None) == before


@pytest.mark.parametrize('fault', ['domain-float', 'domain-bool', 'domain-zero-uuid',
    'activities-float', 'gate-float', 'kind', 'version-bool', 'unknown-version',
    'path', 'platform', 'retired-int', 'extra'])
def test_malformed_volume_marker_refuses_without_rewriting(tmp_path, fault):
    from pm import lifetime
    domain = tmp_path / 'state'
    with lifetime.activity(domain, create=True, stable_identity=True):
        pass
    marker = lifetime.Gate(domain).marker
    record = json.loads(marker.read_bytes())
    if fault.endswith('-float'):
        record[fault.split('-')[0]][1] = float(record[fault.split('-')[0]][1])
    elif fault == 'domain-bool':
        record['domain'][1] = True
    elif fault == 'domain-zero-uuid':
        record['domain'][0] = '00000000-0000-0000-0000-000000000000'
    else:
        key, value = {'kind': ('identity', 'unknown'), 'version-bool': ('version', True),
            'unknown-version': ('version', 3), 'path': ('path', None),
            'platform': ('platform', 'unknown'), 'retired-int': ('retired', 1),
            'extra': ('extra', True)}[fault]
        record[key] = value
    marker.write_text(json.dumps(record))
    before = marker.read_bytes()
    with pytest.raises(lifetime.LifetimeUnavailable):
        with lifetime.activity(domain):
            pass
    assert marker.read_bytes() == before


def test_native_protocol_retirement_and_recreation_remain_guarded(tmp_path):
    from pm import lifetime
    domain = tmp_path / 'state'
    with lifetime.activity(domain, create=True, stable_identity=True):
        pass
    with lifetime.gate(domain) as guard:
        ownership = guard._snapshot()
        domain.rmdir()
        guard.confirm_removed(ownership)
    with lifetime.activity(domain, create=True):
        with lifetime.gate(domain) as guard:
            assert guard.qualified and guard.held()


@pytest.mark.parametrize('target', ['domain', 'activities', 'gate'])
def test_replaced_native_object_refuses_qualification(tmp_path, target):
    from pm import lifetime
    domain = tmp_path / 'state'
    with lifetime.activity(domain, create=True, stable_identity=True):
        pass
    guard = lifetime.Gate(domain)
    path = {'domain': domain, 'activities': guard.activities, 'gate': guard.namespace / 'gate'}[target]
    path.rename(path.with_name(path.name + '.previous'))
    path.mkdir() if target != 'gate' else path.touch()
    before = guard.marker.read_bytes()
    with pytest.raises(lifetime.LifetimeUnavailable, match='identity changed'):
        with lifetime.activity(domain):
            pass
    assert guard.marker.read_bytes() == before


@pytest.mark.parametrize('survivor,replacement', [(True, False), (False, True)])
def test_native_failed_output_preserves_survivors_and_replacements(tmp_path, survivor, replacement):
    from pm import lifetime
    from pm.lifetime_cleanup import defer_failed_output, output_identity
    domain = tmp_path / 'state'
    reader = None
    with lifetime.activity(domain, create=True, stable_identity=True):
        output = domain / 'failed'
        output.mkdir()
        assert defer_failed_output(output, output_identity(output))
        if survivor:
            reader = lifetime.acquire_activity(domain, generation=output)
        if replacement:
            output.rename(domain / 'previous')
            output.mkdir()
    try:
        assert output.is_dir()
    finally:
        if reader is not None:
            reader.close()


def test_gate_path_replacement_during_native_capture_is_not_qualified(tmp_path, monkeypatch):
    from pm import lifetime
    from hermes_platform.host import volume_identity
    domain = tmp_path / 'state'
    with lifetime.activity(domain, create=True, stable_identity=True):
        pass
    with lifetime.gate(domain) as guard:
        original = volume_identity.local_file_identity
        def replaced(fd):
            value = original(fd)
            path = guard.namespace / 'gate'
            path.rename(path.with_name('previous-gate'))
            path.touch()
            return value
        monkeypatch.setattr(volume_identity, 'local_file_identity', replaced)
        assert not guard.qualified


@pytest.mark.parametrize('fact', ['different-volume', 'unavailable'])
def test_unknown_or_changed_native_volume_never_rebinds_a_record(tmp_path, monkeypatch, fact):
    from pm import lifetime
    from hermes_platform.host import volume_identity
    domain = tmp_path / 'state'
    with lifetime.activity(domain, create=True, stable_identity=True):
        pass
    marker = lifetime.Gate(domain).marker
    before = marker.read_bytes()
    def changed(fd):
        if fact == 'unavailable':
            raise OSError('unavailable native fixture')
        return '66666666-6666-4666-8666-666666666666'
    monkeypatch.setattr(volume_identity, 'local_volume_identity', changed)
    with pytest.raises(lifetime.LifetimeUnavailable, match='identity changed'):
        with lifetime.activity(domain):
            pass
    assert marker.read_bytes() == before
