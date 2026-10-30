"""Snapshot reset preserves physical ownership in disposable Desktop fixtures."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from pm.lifetime import activity, Gate

pytestmark = pytest.mark.platforms('posix')


def _world(tmp_path):
    source = Path(__file__).resolve().parents[3] / 'apps/desktop/e2e/update/seed.py'
    spec = importlib.util.spec_from_file_location('snapshot_seed', source)
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    home = tmp_path / 'sb/home/.hermes'
    domain = home / 'installs/fixture'
    with activity(domain, create=True):
        (domain / 'facts.json').write_text('original')
    (tmp_path / 'origin.git').mkdir()
    (tmp_path / 'install.json').write_text(json.dumps({'sandboxRoot': str(tmp_path / 'sb'),
                                                      'hermesHome': str(home),
                                                      'origin': str(tmp_path / 'origin.git'),
                                                      'env': {'HERMES_HOME': str(home)}}))
    (tmp_path / 'golden').mkdir()
    shutil.copytree(tmp_path / 'sb', tmp_path / 'golden' / 'sb')
    shutil.copytree(tmp_path / 'origin.git', tmp_path / 'golden' / 'origin')
    return seed, domain


def test_repeated_restore_keeps_qualification_and_removes_only_fixture_extras(tmp_path):
    seed, domain = _world(tmp_path)
    for _ in range(2):
        (domain / 'facts.json').write_text('changed')
        extra = domain / 'extra/directory'
        extra.mkdir(parents=True)
        (extra / 'data').write_text('temporary')
        seed.restore(tmp_path)
        with activity(domain):
            assert (domain / 'facts.json').read_text() == 'original'
            assert not extra.exists()
        assert Gate(domain).marker.exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_restore_does_not_write_through_fixture_aliases(tmp_path, kind):
    seed, domain = _world(tmp_path)
    outside = tmp_path / 'outside'
    outside.write_text('preserve')
    target = domain / 'facts.json'
    target.unlink()
    if kind == 'symlink':
        target.symlink_to(outside)
    else:
        target.hardlink_to(outside)
    seed.restore(tmp_path)
    assert outside.read_text() == 'preserve'
    assert target.read_text() == 'original'
    with activity(domain):
        pass


@pytest.mark.parametrize('entry', ['domain', 'gate', 'activities'])
def test_replaced_lifetime_identity_requires_reseed_before_restore(tmp_path, entry):
    seed, domain = _world(tmp_path)
    guard = Gate(domain)
    path = {'domain': domain, 'gate': guard.namespace / 'gate', 'activities': guard.activities}[entry]
    held = path.with_name(path.name + '.replaced')
    path.rename(held)
    if held.is_dir():
        path.mkdir()
    else:
        path.touch()
    (tmp_path / 'origin.git/preserve').write_text('not restored after failure')
    with pytest.raises(RuntimeError, match='reseed'):
        seed.restore(tmp_path)
    assert (tmp_path / 'origin.git/preserve').exists()


def test_reader_prevents_fixture_restore(tmp_path):
    seed, domain = _world(tmp_path)
    with activity(domain):
        (domain / 'facts.json').write_text('still in use')
        with pytest.raises(RuntimeError, match='active'):
            seed.restore(tmp_path)
        assert (domain / 'facts.json').read_text() == 'still in use'


def test_parent_traversal_facts_cannot_inspect_or_mutate_outside_lifetimes(tmp_path):
    seed, _ = _world(tmp_path)
    outside_home = tmp_path / 'outside/home/.hermes'
    outside_domain = outside_home / 'installs/fixture'
    namespace = outside_home / 'installs/.lifetimes/fixture'
    with activity(outside_domain, create=True) as owned:
        stale_path, stale_data = owned.path, owned.data
    stale_path.write_bytes(stale_data)

    facts_path = tmp_path / 'install.json'
    facts = json.loads(facts_path.read_text())
    escaped_home = str(tmp_path / 'sb/../outside/home/.hermes')
    facts['hermesHome'] = escaped_home
    facts['env']['HERMES_HOME'] = escaped_home
    facts_path.write_text(json.dumps(facts))

    expected_namespace = tmp_path / 'golden/sb/../outside/home/.hermes/installs/.lifetimes/fixture'
    expected_namespace.mkdir(parents=True)
    (expected_namespace / 'domain.json').write_bytes((namespace / 'domain.json').read_bytes())
    (tmp_path / 'golden/sb/../outside/home/.hermes/installs/fixture').mkdir(parents=True)

    marker = namespace / 'domain.json'
    marker_bytes = marker.read_bytes()
    activity_bytes = stale_path.read_bytes()
    marker_identity = (marker.stat().st_dev, marker.stat().st_ino)
    activity_identity = (stale_path.stat().st_dev, stale_path.stat().st_ino)
    gate_path = namespace / 'gate'
    gate_bytes = gate_path.read_bytes()
    gate_identity = (gate_path.stat().st_dev, gate_path.stat().st_ino)

    with pytest.raises(RuntimeError, match='facts|path|sandbox|reseed'):
        seed.restore(tmp_path)

    assert marker.read_bytes() == marker_bytes
    assert (marker.stat().st_dev, marker.stat().st_ino) == marker_identity
    assert stale_path.read_bytes() == activity_bytes
    assert (stale_path.stat().st_dev, stale_path.stat().st_ino) == activity_identity
    assert gate_path.read_bytes() == gate_bytes
    assert (gate_path.stat().st_dev, gate_path.stat().st_ino) == gate_identity


def test_symlink_domain_is_rejected_before_outside_lifetime_gate_access(tmp_path, monkeypatch):
    seed, domain = _world(tmp_path)
    outside_domain = tmp_path / 'outside/home/.hermes/installs/fixture'
    with activity(outside_domain, create=True) as owned:
        stale_path, stale_data = owned.path, owned.data
    stale_path.write_bytes(stale_data)
    stale_identity = (stale_path.stat().st_dev, stale_path.stat().st_ino)

    original = domain.with_name('fixture-original')
    domain.rename(original)
    domain.symlink_to(outside_domain, target_is_directory=True)
    monkeypatch.setattr(seed, 'gate', lambda *args, **kwargs: pytest.fail('gate accessed before path validation'))

    with pytest.raises(RuntimeError, match='directory|reseed'):
        seed.restore(tmp_path)

    assert stale_path.read_bytes() == stale_data
    assert (stale_path.stat().st_dev, stale_path.stat().st_ino) == stale_identity
    assert domain.is_symlink()
