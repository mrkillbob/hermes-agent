"""Native lifetime references; Windows sharing holds survive handle inheritance."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import copy
import errno
from dataclasses import dataclass
import os
from pathlib import Path
import stat


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


def _windows_identity(handle, *, legacy=False):
    """Select one native identity protocol, independent of the calling Python."""
    import ctypes
    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                    ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    class FileIdInformation(ctypes.Structure):
        _fields_ = [("volume", ctypes.c_ulonglong), ("identifier", ctypes.c_ubyte * 16)]

    api = _windows()
    api.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    api.GetFileInformationByHandle.restype = wintypes.BOOL
    previous = FileInformation()
    if not api.GetFileInformationByHandle(handle, ctypes.byref(previous)):
        raise ctypes.WinError(ctypes.get_last_error())
    if legacy:
        return [previous.volume, (previous.index_high << 32) | previous.index_low], previous.attributes
    api.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               wintypes.LPVOID, wintypes.DWORD]
    api.GetFileInformationByHandleEx.restype = wintypes.BOOL
    current = FileIdInformation()
    if not api.GetFileInformationByHandleEx(handle, 18, ctypes.byref(current), ctypes.sizeof(current)):
        raise ctypes.WinError(ctypes.get_last_error())
    return [current.volume, int.from_bytes(current.identifier, "little")], previous.attributes


def file_identity(fd, *, legacy=False):
    if os.name == "nt":
        return _windows_identity(native_reference(fd), legacy=legacy)[0]
    info = os.fstat(fd)
    return [info.st_dev, info.st_ino]


def directory_identity(path, *, legacy=False):
    if os.name != "nt":
        info = Path(path).lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("lifetime path is not a concrete directory")
        return [info.st_dev, info.st_ino]
    import ctypes

    api = _windows()
    # Inspect the named object itself and allow ordinary concurrent readers/writers.
    handle = api.CreateFileW(str(path), 0x80, 7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        identity, attributes = _windows_identity(handle, legacy=legacy)
        if not attributes & 0x10 or attributes & 0x400:
            raise ValueError("lifetime path is not a concrete directory")
        return identity
    finally:
        api.CloseHandle(handle)


def _descriptor_flags(required):
    """Snapshot before mutation; /dev/fd enumeration can include its already closed fd."""
    for directory in ('/proc/self/fd', '/dev/fd'):
        try:
            names = os.listdir(directory)
            break
        except (FileNotFoundError, NotADirectoryError):
            continue
    else:
        raise RuntimeError('cannot inspect bootstrap descriptors before exec')
    candidates = {int(name) for name in names if name.isdecimal()} | set(required)
    flags = {}
    for fd in candidates:
        if fd <= 2 and fd not in required:
            continue
        try:
            inherited, info = os.get_inheritable(fd), os.fstat(fd)
            flags[fd] = (inherited, info.st_dev, info.st_ino, info.st_mode)
        except OSError as error:
            if error.errno != errno.EBADF or fd in required:
                raise
    return flags


def _restore_descriptor(fd, previous, required):
    try:
        info = os.fstat(fd)
    except OSError as error:
        if error.errno == errno.EBADF and fd not in required:
            return
        raise
    if (info.st_dev, info.st_ino, info.st_mode) != previous[1:]:
        raise RuntimeError('bootstrap descriptor identity changed during exec')
    os.set_inheritable(fd, previous[0])


@contextmanager
def exec_descriptors(references):
    """CLI-only POSIX exec custody, with close_fds semantics and complete failure rollback."""
    import signal

    required = frozenset(references)
    flags = _descriptor_flags(required)
    resets = [(number, signal.getsignal(number)) for name in ('SIGPIPE', 'SIGXFZ', 'SIGXFSZ')
              if (number := getattr(signal, name, None)) is not None]
    with ExitStack() as rollback:
        for fd, previous in flags.items():
            wanted = fd in required
            if previous[0] != wanted:
                rollback.callback(_restore_descriptor, fd, previous, required)
                os.set_inheritable(fd, wanted)
        for number, handler in resets:
            if handler == signal.SIG_IGN:
                rollback.callback(signal.signal, number, handler)
                signal.signal(number, signal.SIG_DFL)
        yield
