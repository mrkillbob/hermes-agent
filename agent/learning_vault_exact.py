"""Descriptor-contained implementation of the exact-note library contract.

Only trusted host code selects the authorization policy; this module neither
mints approvals nor authenticates arbitrary Python callers. No live host is wired.
"""
from __future__ import annotations

from contextlib import ExitStack
import errno
import hashlib
import os
from pathlib import PurePosixPath
import re
import stat
from types import FunctionType
from typing import Callable

from agent.learning_vault import (
    ExactVaultNoteRequest, MAX_NOTE_BYTES, VaultLearningDiagnostic,
    VaultLearningNode, _CATALOG_ROOT_PARTS, _node_from_text,
)


def _paths_valid(root: str, relative: str) -> bool:
    if any(not value or len(value) > 4096 or "\\" in value or "\0" in value
           for value in (root, relative)):
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in root + relative):
        return False
    base, note = PurePosixPath(root), PurePosixPath(relative)
    if root.startswith("//") or not base.is_absolute() or note.is_absolute():
        return False
    if base.as_posix() != root or note.as_posix() != relative:
        return False
    if any(part in (".", "..") for part in (*base.parts, *note.parts)):
        return False
    if len(base.parts) > 64 or len(note.parts) > 64 or note.suffix != ".md":
        return False
    return any(len(note.parts) > len(prefix) and note.parts[:len(prefix)] == prefix
               for prefix in _CATALOG_ROOT_PARTS)


def _valid_request(request: object) -> bool:
    if type(request) is not ExactVaultNoteRequest:
        return False
    strings = (request.vault_root, request.relative_path,
               request.expected_record_id, request.expected_sha256)
    numbers = (request.root_device, request.root_inode, request.max_bytes)
    if any(type(value) is not str for value in strings):
        return False
    if any(type(value) is not int for value in numbers):
        return False
    if request.root_device < 0 or request.root_inode <= 0:
        return False
    if not 1 <= request.max_bytes <= MAX_NOTE_BYTES:
        return False
    identity = request.expected_record_id
    if not identity or len(identity) > 256 or identity != identity.strip():
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in identity):
        return False
    return (re.fullmatch(r"[0-9a-f]{64}", request.expected_sha256) is not None
            and _paths_valid(request.vault_root, request.relative_path))


def _supports_wrapped(function, capabilities) -> bool:
    """Recognize up to 16 transparent wrappers without replacing guarded I/O.

    Wrapper metadata is trusted in-process introspection, not authorization or
    proof that arbitrary Python wrappers faithfully implement the OS contract.
    Opaque, cyclic and overlong chains fail closed.
    """
    seen = set()
    for _ in range(17):
        if id(function) in seen:
            return False
        seen.add(id(function))
        if any(function is supported for supported in capabilities):
            return True
        if type(function) is not FunctionType:
            return False
        function = vars(function).get("__wrapped__")
    return False


def _safe_descriptors() -> bool:
    return (all(getattr(os, flag, 0) for flag in
                ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"))
            and _supports_wrapped(os.open, os.supports_dir_fd)
            and _supports_wrapped(os.stat, os.supports_dir_fd)
            and _supports_wrapped(os.stat, os.supports_follow_symlinks))


def _identity(info) -> tuple[int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode


def _file_state(info) -> tuple:
    return (_identity(info), info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _entries_stable(entries) -> bool:
    return all(_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity
               for parent, name, identity in entries)


def _open_child(stack, parent: int, name: str, flags: int, entries) -> int:
    fd = os.open(name, flags, dir_fd=parent)
    stack.callback(os.close, fd)
    entries.append((parent, name, _identity(os.fstat(fd))))
    return fd


def _capture(stack, request, entries):
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open("/", directory_flags)
    stack.callback(os.close, parent)
    for part in PurePosixPath(request.vault_root).parts[1:]:
        parent = _open_child(stack, parent, part, directory_flags, entries)
    root_info = os.fstat(parent)
    if (root_info.st_dev, root_info.st_ino) != (request.root_device, request.root_inode):
        return None, None, None, "vault_identity_mismatch"
    parts = PurePosixPath(request.relative_path).parts
    for part in parts[:-1]:
        parent = _open_child(stack, parent, part, directory_flags, entries)
    fd = _open_child(stack, parent, parts[-1],
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, entries)
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        return None, None, None, "note_not_regular"
    if before.st_nlink != 1:
        return None, None, None, "note_hardlinked"
    if before.st_size > request.max_bytes:
        return None, None, None, "note_too_large"
    raw = os.read(fd, request.max_bytes + 1)
    if len(raw) > request.max_bytes:
        return None, None, None, "note_too_large"
    if len(raw) != before.st_size:
        return None, None, None, "note_changed"
    return raw, fd, before, None


def _parse(raw, request, info):
    if hashlib.sha256(raw).hexdigest() != request.expected_sha256:
        return None, VaultLearningDiagnostic("note_hash_mismatch", request.relative_path)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, VaultLearningDiagnostic("invalid_utf8", request.relative_path)
    try:
        node, diagnostic = _node_from_text(
            text, request.relative_path, fallback_mtime=info.st_mtime)
    except Exception:  # health: allow BLE001 -- Bound parser failures without disclosing note data or exception text.
        return None, VaultLearningDiagnostic("note_parse_failed", request.relative_path)
    if node is not None and node.record_id != request.expected_record_id:
        return None, VaultLearningDiagnostic("record_id_mismatch", request.relative_path)
    return node, diagnostic


def _request_values(request):
    return (request.vault_root, request.root_device, request.root_inode,
            request.relative_path, request.expected_record_id,
            request.expected_sha256, request.max_bytes)


def read_exact(
    request: ExactVaultNoteRequest,
    *,
    authorize: Callable[[ExactVaultNoteRequest], bool],
) -> tuple[VaultLearningNode | None, VaultLearningDiagnostic | None]:
    """No target filesystem access until pure validation and trusted policy approve."""
    if type(request) is not ExactVaultNoteRequest:
        return None, VaultLearningDiagnostic("invalid_request")
    selected = ExactVaultNoteRequest(*_request_values(request))
    if not _valid_request(selected):
        return None, VaultLearningDiagnostic("invalid_request")
    # Validate, authorize and consume one private snapshot. Trusted host policies
    # must neither mutate it nor start side effects; frozen is not a sandbox.
    bound_values = _request_values(selected)
    try:
        approved = authorize(selected) is True
    except Exception:  # health: allow BLE001 -- Trusted policy errors must deny without leaking arbitrary exception text.
        approved = False
    if (not approved or not _valid_request(selected)
            or _request_values(selected) != bound_values):
        return None, VaultLearningDiagnostic("authorization_denied")
    if not _safe_descriptors():
        return None, VaultLearningDiagnostic("unsupported_safe_descriptors")
    label = selected.relative_path
    try:
        with ExitStack() as stack:
            entries = []
            raw, fd, before, reason = _capture(stack, selected, entries)
            if reason:
                return None, VaultLearningDiagnostic(reason, label)
            if _file_state(before) != _file_state(os.fstat(fd)) or not _entries_stable(entries):
                return None, VaultLearningDiagnostic("note_changed", label)
            node, diagnostic = _parse(raw, selected, before)
            if _file_state(before) != _file_state(os.fstat(fd)) or not _entries_stable(entries):
                return None, VaultLearningDiagnostic("note_changed", label)
            return node, diagnostic
    except OSError as exc:
        reason = "unsafe_note_path" if exc.errno in (errno.ELOOP, errno.ENOTDIR) else "note_unreadable"
        return None, VaultLearningDiagnostic(reason, label)
    except (ValueError, NotImplementedError):
        return None, VaultLearningDiagnostic("unsupported_safe_descriptors", label)
