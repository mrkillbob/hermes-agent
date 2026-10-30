"""Failed producers must preserve outputs still held by their managed children."""
import pytest

from pm import lifetime, operations


@pytest.mark.parametrize('survivor', [False, True])
@pytest.mark.parametrize('aliased', [False, pytest.param(True, marks=pytest.mark.platforms('posix'))])
def test_failed_side_build_cleans_only_after_its_last_activity(tmp_path, monkeypatch, survivor, aliased):
    base = tmp_path / 'base' / 'python'
    base.parent.mkdir()
    base.touch()
    monkeypatch.setattr('pm._uv._toolchain', lambda **kwargs: (None, base))
    storage = tmp_path / 'storage'
    storage.mkdir()
    alias = tmp_path / 'alias'
    if aliased:
        alias.symlink_to(storage, target_is_directory=True)
    root = (alias if aliased else storage) / 'side'
    built, readers = [], []

    def build(generation, python):
        built.append(generation)
        if survivor:
            readers.append(lifetime.acquire_activity(root, generation=generation))
        raise ValueError('synthetic failed build')

    try:
        with pytest.raises(ValueError, match='synthetic failed build'):
            operations._ensure_generation('proof', root, {}, build, record={}, explicit=True)
        assert built[0].exists() is survivor
        assert not (root / 'active.json').exists()
    finally:
        for reader in readers:
            reader.close()


def test_nested_failed_output_keeps_its_ancestor_reader(tmp_path):
    from pm.lifetime_cleanup import defer_failed_output, output_identity

    state = tmp_path / 'state'
    with lifetime.activity(state, create=True):
        generation = state / 'environments' / 'generation'
        output = generation / 'venv'
        output.mkdir(parents=True)
        reader = lifetime.acquire_activity(state, generation=generation)
        assert defer_failed_output(output, output_identity(output))
    try:
        assert output.is_dir()
    finally:
        reader.close()


def test_failure_cleanup_preserves_replaced_output_identity(tmp_path):
    from pm.lifetime_cleanup import defer_failed_output, output_identity

    state = tmp_path / 'state'
    with lifetime.activity(state, create=True):
        output = state / 'created'
        output.mkdir()
        assert defer_failed_output(output, output_identity(output))
        output.rename(state / 'retained-original')
        output.mkdir()
        (output / 'unique.txt').write_text('replacement must survive')
    assert (output / 'unique.txt').read_text() == 'replacement must survive'
    assert (state / 'retained-original').is_dir()


def test_changed_nested_output_prevents_parent_cleanup(tmp_path):
    from pm.lifetime_cleanup import defer_failed_output, output_identity

    state = tmp_path / 'state'
    with lifetime.activity(state, create=True):
        generation = state / 'generation'
        output = generation / 'venv'
        output.mkdir(parents=True)
        assert defer_failed_output(output, output_identity(output))
        assert defer_failed_output(generation, output_identity(generation))
        output.rename(generation / 'retained-original')
        output.mkdir()
        (output / 'unique.txt').write_text('new descendant must survive parent cleanup')
    assert (output / 'unique.txt').is_file()
