"""Board coordination preserves mutation guards and does not serialize unrelated boards."""
from pathlib import Path
from threading import Event, Thread

import pytest
from hermes_cli import kanban_db
from hermes_cli.kanban_db_boards import (
    board_ownership_lock,
    board_project_ownership_lock,
)


@pytest.fixture(autouse=True)
def board_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)


def test_mutation_refusal_precedes_lock_or_directory_creation(tmp_path, monkeypatch):
    def refuse(target):
        raise PermissionError('existing mutation fence')
    monkeypatch.setattr(kanban_db, '_assert_not_delegated_child_mutation', refuse)
    directory = kanban_db.board_dir('not-created')
    with pytest.raises(PermissionError, match='existing mutation fence'):
        kanban_db.write_board_metadata('not-created', project_id='p_owned')
    assert not directory.exists()


def test_default_metadata_refusal_precedes_catalog_lock_creation(tmp_path, monkeypatch):
    def refuse(target):
        raise PermissionError('existing mutation fence')

    monkeypatch.setattr(kanban_db, '_assert_not_delegated_child_mutation', refuse)
    runtime = tmp_path / 'runtime'
    with pytest.raises(PermissionError, match='existing mutation fence'):
        kanban_db.write_board_metadata(None, name='No mutation')
    assert not runtime.exists()


def test_catalog_is_a_valid_board_slug_without_lock_recursion():
    meta = kanban_db.create_board('catalog', project_id='p_catalog')
    assert meta['project_id'] == 'p_catalog'
    assert kanban_db.read_board_metadata('catalog')['project_id'] == 'p_catalog'


def test_different_board_writer_remains_independent():
    done = Event()
    errors = []
    def write_other():
        try:
            kanban_db.write_board_metadata('other', project_id='p_other')
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()
    worker = Thread(target=write_other, daemon=True)
    with board_ownership_lock('held'):
        worker.start()
        independent = done.wait(5)
    worker.join(10)
    assert independent and not errors
    assert kanban_db.read_board_metadata('other')['project_id'] == 'p_other'


def test_public_display_metadata_marker_waits_for_catalog_inventory_lock():
    # A display-only update can publish board.json into a DB-only directory.
    # That changes list_boards() even though project_id is omitted, so it must
    # be fenced by the same catalog lock as owner-bearing creation.
    directory = kanban_db.board_dir('late-marker')
    directory.mkdir(parents=True)
    started = Event()
    done = Event()
    errors = []

    def publish_marker():
        started.set()
        try:
            kanban_db.write_board_metadata('late-marker', name='Late Marker')
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    with board_project_ownership_lock():
        worker = Thread(target=publish_marker, daemon=True)
        worker.start()
        assert started.wait(5)
        assert not done.wait(0.1)
        assert not kanban_db.board_metadata_path('late-marker').exists()
    worker.join(10)
    assert done.is_set() and not errors
    assert kanban_db.read_board_metadata('late-marker')['name'] == 'Late Marker'


def test_direct_internal_writer_retains_mutation_fence(monkeypatch):
    from hermes_cli.kanban_db_boards import _write_board_metadata
    def refuse(target):
        raise PermissionError('existing mutation fence')
    monkeypatch.setattr(kanban_db, '_assert_not_delegated_child_mutation', refuse)
    directory = kanban_db.board_dir('not-created')
    with pytest.raises(PermissionError, match='existing mutation fence'):
        _write_board_metadata('not-created', project_id='p_owned')
    assert not directory.exists()


@pytest.mark.parametrize('archive', [True, False])
def test_board_removal_waits_for_owned_creation_and_preserves_lock_inode(monkeypatch, archive):
    from hermes_cli import active_sessions
    kanban_db.create_board('owned', project_id='p_owned')
    attempted = Event()
    done = Event()
    errors = []
    original_flock = active_sessions._flock

    def observe_attempt(fh, *, lock):
        if lock:
            attempted.set()
        return original_flock(fh, lock=lock)

    def remove():
        try:
            kanban_db.remove_board('owned', archive=archive)
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    with board_ownership_lock('owned') as held:
        lock_path = held.path
        inode = lock_path.stat().st_ino
        monkeypatch.setattr(active_sessions, '_flock', observe_attempt)
        worker = Thread(target=remove, daemon=True)
        worker.start()
        assert attempted.wait(5)
        assert not done.is_set()
        assert kanban_db.read_board_metadata('owned')['archived'] is False
        assert kanban_db.board_metadata_path('owned').exists()
    worker.join(10)
    assert done.is_set() and not errors
    assert lock_path.stat().st_ino == inode
    if archive:
        assert kanban_db.read_board_metadata('owned')['archived'] is True
    else:
        assert not kanban_db.board_dir('owned').exists()
    kanban_db.create_board('owned', project_id='p_new')
    with board_ownership_lock('owned') as recreated:
        assert recreated.path == lock_path
        assert recreated.path.stat().st_ino == inode
    assert kanban_db.read_board_metadata('owned')['project_id'] == 'p_new'
