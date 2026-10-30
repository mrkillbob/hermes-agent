"""Whole-state collection respects outside-state activity and legacy ownership."""
from pathlib import Path

from pm import install_states, lifetime


def _state(tmp_path):
    state = tmp_path / "home" / "installs" / "new"
    with lifetime.activity(state, create=True):
        inputs = state / "inputs"
        inputs.mkdir()
        (inputs / ".project-root").write_text(str(tmp_path / "home" / "gone"))
        (state / "environments" / "generation").mkdir(parents=True)
    return state


def test_reader_activity_prevents_collector_delete_intent(tmp_path, monkeypatch):
    state = _state(tmp_path)
    removals = []
    monkeypatch.setattr(install_states.shutil, "rmtree", lambda path, **kw: removals.append(path))
    with lifetime.activity(state, generation=state / "environments" / "generation"):
        assert install_states.collect_orphan_install_states(state.parent) == []
        assert not removals


def test_legacy_orphan_is_retained_without_qualification(tmp_path, monkeypatch):
    state = tmp_path / "home" / "installs" / "legacy"
    inputs = state / "inputs"
    inputs.mkdir(parents=True)
    (inputs / ".project-root").write_text(str(tmp_path / "home" / "gone"))
    removals = []
    monkeypatch.setattr(install_states.shutil, "rmtree", lambda path, **kw: removals.append(path))
    assert install_states.collect_orphan_install_states(state.parent) == []
    assert not removals


def test_idle_qualified_orphan_records_successful_retirement(tmp_path):
    state = _state(tmp_path)
    assert install_states.collect_orphan_install_states(state.parent) == [state]
    with lifetime.activity(state, create=True):
        with lifetime.gate(state) as guard:
            assert guard.qualified


def test_collector_first_late_reader_cannot_register_removed_target(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import pytest

    state = _state(tmp_path)
    generation = state / 'environments' / 'generation'
    entered, attempted, proceed = Event(), Event(), Event()
    original = install_states._remove_state

    def remove(path):
        entered.set()
        assert proceed.wait(10)
        return original(path)

    def late_reader():
        attempted.set()
        return lifetime.acquire_activity(state, generation=generation)

    monkeypatch.setattr(install_states, '_remove_state', remove)
    with ThreadPoolExecutor(max_workers=2) as pool:
        collector = pool.submit(install_states.collect_orphan_install_states, state.parent)
        assert entered.wait(10)
        reader = pool.submit(late_reader)
        assert attempted.wait(10)
        proceed.set()
        assert collector.result(timeout=10) == [state]
        with pytest.raises(lifetime.LifetimeUnavailable):
            reader.result(timeout=10)


def test_live_builder_preserves_orphan_before_internal_lock_creation(tmp_path):
    state = _state(tmp_path)
    with lifetime.activity(state):
        assert not (state / '.install.lock').exists()
        assert install_states.collect_orphan_install_states(state.parent) == []
        assert state.is_dir()
