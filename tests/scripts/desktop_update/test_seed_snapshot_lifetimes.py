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
