"""Recovery guidance must not discard a generation whose capture failed."""
from unittest.mock import Mock

import pytest

from hermes_state import SessionDB
from hermes_state_dbfile import RetiredGenerationCaptureError
from hermes_state_errors import DeletedWalGenerationError, StateDbReplacedError, classify_persistence_error
from hermes_state_user_copy import describe_storage_failure


def test_failed_capture_does_not_promise_recovery_or_request_shutdown():
    # Exercise the real halt branch without opening a database or creating processes.
    db = Mock(spec=SessionDB)
    db.db_path = "fixture-state.db"
    db._db_replaced = False
    db._db_file_was_replaced.return_value = False
    db._db_wal_generation_lost = False
    db._wal_generation_was_lost.return_value = True
    db._capture_retired_generation.side_effect = RetiredGenerationCaptureError("fixture capture failed")
    with pytest.raises(DeletedWalGenerationError) as raised:
        SessionDB._halt_if_db_generation_changed(db)
    db._capture_retired_generation.assert_called_once_with("halt")
    db._disable_close_time_checkpoint.assert_called_once_with()
    db.close.assert_not_called()
    assert db._db_wal_generation_lost is True
    assert classify_persistence_error(str(raised.value)) == "deleted_wal"
    text = str(raised.value)
    assert "Recovery is not yet verified" in text
    assert "Keep the processes" in text
    assert "durable recovery copy" in text
    assert "Nothing is lost" not in text
    assert "quit every" not in text.lower()


@pytest.mark.parametrize("error", [
    DeletedWalGenerationError("deleted state.db-wal or state.db-shm"),
    StateDbReplacedError("state.db was replaced"),
])
def test_user_copy_preserves_holders_and_stable_gui_code(error):
    failure = describe_storage_failure(error)
    assert failure.code == "storage_replaced"
    assert "Keep the processes" in failure.action
    assert "durable recovery copy" in failure.action
    assert "Nothing is lost" not in failure.action
    assert "gateway stop" not in failure.action
    assert "quit every" not in failure.action.lower()
