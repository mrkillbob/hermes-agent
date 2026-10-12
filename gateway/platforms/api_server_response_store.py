"""Owner-only SQLite opening for the API response history store."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def connect_response_store(db_path: str) -> sqlite3.Connection:
    """Create and tighten the history file before SQLite can populate it."""
    if db_path != ":memory:":
        from hermes_state import _secure_state_db_files
        try:
            # Exclusive creation never opens/closes an existing inode holding SQLite locks.
            _secure_state_db_files(Path(db_path), create_main=True)
        except OSError as exc:
            logger.warning("Could not restrict response store permissions for %s: %s", db_path, exc)
    return sqlite3.connect(db_path, check_same_thread=False)


def tighten_response_store_file_permissions(db_path: str | None) -> None:
    """Force owner-only permissions on the DB and SQLite sidecars."""
    if not db_path:
        return
    for candidate in (Path(db_path), Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        try:
            if candidate.exists():
                candidate.chmod(0o600)
        except OSError:
            logger.debug("Failed to restrict response store permissions for %s", candidate, exc_info=True)
