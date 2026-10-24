"""Native lifetime references; Windows sharing holds survive handle inheritance."""
from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import dataclass
import os
from pathlib import Path


def _windows():
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.GetCurrentProcess.restype = wintypes.HANDLE
    api.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                   ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                   wintypes.BOOL, wintypes.DWORD]
    api.DuplicateHandle.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def open_hold(path: Path, *, create: bool = False, reader: bool = False) -> int | None:
    """Own a new reference or exclusively probe an existing one; None means held/unknown."""
    if os.name == "nt":
        import ctypes
        import msvcrt

        api = _windows()
        handle = api.CreateFileW(str(path), 0xC0000000, 1 if (create or reader) else 0, None,
                                 1 if create else 3, 0x00200080, None)
        if handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            if not create and error in (5, 32, 33):
                return None
            raise ctypes.WinError(error)
        try:
            return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            api.CloseHandle(handle)
            raise
    from pm.filesystem import lock_fd

    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        if lock_fd(fd, wait=False):
            return fd
    except BaseException:
        os.close(fd)
        raise
    os.close(fd)
    return None


@dataclass(frozen=True)
class Handoff:
    options: dict
    references: tuple[int, ...]


@contextmanager
def inherit(references, options=None):
    """Explicitly hand only lifetime references to a managed child; do not unlock on close."""
    options = dict(options or {})
    references = tuple(references)
    if not references:
        yield Handoff(options, ())
        return
    if os.name != "nt":
        options["pass_fds"] = tuple(sorted(set(options.get("pass_fds", ())) | set(references)))
        options["close_fds"] = True
        yield Handoff(options, tuple(references))
        return
    import ctypes
    import subprocess
    from ctypes import wintypes

    api = _windows()
    process = api.GetCurrentProcess()
    copies = []
    try:
        for handle in references:
            duplicate = wintypes.HANDLE()
            if not api.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, True, 2):
                raise ctypes.WinError(ctypes.get_last_error())
            copies.append(duplicate.value)
        startup = copy.copy(options.get("startupinfo") or subprocess.STARTUPINFO())
        attributes = dict(startup.lpAttributeList or {})
        attributes["handle_list"] = list(dict.fromkeys([*attributes.get("handle_list", []), *copies]))
        startup.lpAttributeList = attributes
        options.update(startupinfo=startup, close_fds=True)
        yield Handoff(options, tuple(copies))
    finally:
        for handle in copies:
            api.CloseHandle(handle)


def native_reference(fd: int) -> int:
    if os.name == "nt":
        import msvcrt
        return msvcrt.get_osfhandle(fd)
    return fd
