"""Operator-attested opaque stores remain in workspace ownership scans.

Registration supplies physical-store custody, never a historical board/project
identity or execution admission. No database is opened or altered here.
"""
import json
import os
from pathlib import Path
import re
import stat

DIRECTORY = "legacy-empty-archives-v1"
SCHEMA = "hermes.opaque-empty-archives.v1"
REQUIRED = frozenset(("tasks", "task_links", "task_comments", "task_events", "task_runs",
                      "task_attachments", "kanban_notify_subs"))


def registration_path(archive_root: Path) -> Path:
    return archive_root.parent.parent / "quarantine" / DIRECTORY / "registration.json"


def _owner_uid():
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        raise OSError("opaque archive host is unqualified")
    return getuid()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _identity_fields(value):
    if not isinstance(value, list) or len(value) != 9 or any(type(item) is not int for item in value):
        raise OSError("opaque archive identity fields are invalid")
    return value


def _zero_counts(value):
    if not isinstance(value, dict) or not value or len(value) > 64 or not REQUIRED.issubset(value):
        raise OSError("opaque archive count attestation is incomplete")
    if any(type(count) is not int or count != 0 for count in value.values()):
        raise OSError("opaque archive count attestation is not empty")


def _validate_record(record, names):
    if not isinstance(record, dict):
        raise OSError("opaque archive registration record is invalid")
    name = record.get("directory")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", name) or name in names:
        raise OSError("opaque archive registration path is invalid")
    _zero_counts(record.get("counts"))
    _identity_fields(record.get("directory_identity"))
    files = record.get("files")
    if not isinstance(files, dict) or not isinstance(files.get("kanban.db"), dict):
        raise OSError("opaque archive database record is invalid")
    _identity_fields(files["kanban.db"].get("identity"))
    for digest in (files["kanban.db"].get("sha256"), record.get("evidence_sha256")):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise OSError("opaque archive evidence digest is invalid")
    for flag in ("original_data_version_changed", "evidence_data_version_changed"):
        if type(record.get(flag)) is not bool:
            raise OSError("opaque archive data-version history is missing")
    return name


def _validate_registration(value):
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or value.get("scope") != "physical_store_only" or value.get("execution_admission") is not False):
        raise OSError("opaque archive registration schema is invalid")
    records = value.get("records")
    if not isinstance(records, list) or not 1 <= len(records) <= 2:
        raise OSError("opaque archive registration count is invalid")
    names = set()
    for record in records:
        names.add(_validate_record(record, names))
    return value


def _read_registration(path):
    if path.parent.resolve() != path.parent or os.name != "posix":
        raise OSError("opaque archive registration host or parent is unqualified")
    for parent in (path.parent, path.parent.parent):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != _owner_uid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise OSError("opaque archive registration custody is invalid")
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != _owner_uid()
            or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size > 16384):
        raise OSError("opaque archive registration file is invalid")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if _identity(os.fstat(fd)) != _identity(before):
            raise OSError("opaque archive registration changed while opening")
        raw = os.read(fd, 16385)
        if (len(raw) != before.st_size or _identity(os.fstat(fd)) != _identity(before)
                or _identity(path.lstat()) != _identity(before)):
            raise OSError("opaque archive registration changed while reading")
    finally:
        os.close(fd)
    return _validate_registration(json.loads(raw))


def _require_database_identity(database, directory_info, archive_root, expected):
    info = database.lstat()
    # No extra DB descriptor: closing one could drop a caller's POSIX locks.
    if (database.resolve() != database or not stat.S_ISREG(info.st_mode)
            or info.st_uid != _owner_uid() or info.st_nlink != 1
            or list(_identity(info)[1:]) != expected[1:]
            or info.st_dev != directory_info.st_dev or info.st_dev != archive_root.stat().st_dev):
        raise OSError("opaque archive database changed")
    return info


def _require_quiet_sidecars(database, device):
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = database.with_name(database.name + suffix)
        if sidecar.is_symlink():
            raise OSError("opaque archive sidecar is aliased")
        if not sidecar.exists():
            continue
        state = sidecar.lstat()
        if (not stat.S_ISREG(state.st_mode) or state.st_uid != _owner_uid() or state.st_nlink != 1
                or state.st_dev != device or suffix != "-shm" and state.st_size != 0):
            raise OSError("opaque archive has new or unqualified journal state")


def _require_store(record, directory):
    marker = directory / "board.json"
    if directory.resolve() != directory or marker.is_symlink() or marker.exists():
        raise OSError("opaque archive path identity changed")
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != _owner_uid()
            or list(_identity(info)[1:5]) != record["directory_identity"][1:5]):
        raise OSError("opaque archive directory identity changed")
    database = directory / "kanban.db"
    database_info = _require_database_identity(database, info, directory.parent, record["files"]["kanban.db"]["identity"])
    _require_quiet_sidecars(database, database_info.st_dev)


def admits_opaque_store(directory: Path) -> bool:
    """Admit the exact attested store to the existing conservative SQL scan."""
    path = registration_path(directory.parent)
    if not path.exists() and not path.is_symlink():
        return False
    revoked = path.parent / "revoked.json"
    if revoked.exists() or revoked.is_symlink():
        raise OSError("opaque archive registration is revoked")
    try:
        value = _read_registration(path)
        record = next((row for row in value["records"] if row["directory"] == directory.name), None)
        if record is None:
            return False
        _require_store(record, directory)
        return True
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise OSError("opaque archive registration cannot be verified") from error
