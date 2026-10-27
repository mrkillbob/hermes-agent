"""Darwin posix_spawn for detached children without fork handlers in the parent."""
from __future__ import annotations

import contextlib
import ctypes
import functools
import os
import signal
import sys
from collections.abc import Mapping, Sequence

from hermes_platform.host.facts import os_family

# Darwin SDK sys/spawn.h; opaque handles are void* in spawn.h.
_SETSIGDEF = 0x0004
_SETSID = 0x0400
_CLOEXEC_DEFAULT = 0x4000
_HANDLE = ctypes.POINTER(ctypes.c_void_p)
_STRINGS = ctypes.POINTER(ctypes.c_char_p)


@functools.cache
def _native_api():
    api = ctypes.CDLL(None)
    chdir = getattr(api, "posix_spawn_file_actions_addchdir", None)
    if chdir is None:
        chdir = api.posix_spawn_file_actions_addchdir_np
    api.posix_spawn_file_actions_addchdir = chdir
    signatures = {
        "posix_spawn_file_actions_init": [_HANDLE],
        "posix_spawn_file_actions_destroy": [_HANDLE],
        "posix_spawn_file_actions_addchdir": [_HANDLE, ctypes.c_char_p],
        "posix_spawn_file_actions_adddup2": [_HANDLE, ctypes.c_int, ctypes.c_int],
        "posix_spawn_file_actions_addclose": [_HANDLE, ctypes.c_int],
        "posix_spawnattr_init": [_HANDLE],
        "posix_spawnattr_destroy": [_HANDLE],
        "posix_spawnattr_setflags": [_HANDLE, ctypes.c_short],
        "posix_spawnattr_setsigdefault": [_HANDLE, ctypes.POINTER(ctypes.c_uint32)],
        "posix_spawn": [ctypes.POINTER(ctypes.c_int), ctypes.c_char_p,
                        _HANDLE, _HANDLE, _STRINGS, _STRINGS],
    }
    for name, arguments in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, ctypes.c_int
    return api


def _check(error: int) -> None:
    # The spawn APIs return errno directly, independently of the thread's errno.
    if error:
        raise OSError(error, os.strerror(error))


def _cstring(value) -> bytes:
    encoded = os.fsencode(value)
    if b"\0" in encoded:
        raise ValueError("embedded null byte in worker spawn input")
    return encoded


def _vectors(argv: Sequence[str], env: Mapping[str, str]):
    if not argv or not os.path.isabs(argv[0]):
        raise ValueError("native worker launch requires an absolute executable")
    arguments = [_cstring(arg) for arg in argv]
    environment = []
    for name, value in env.items():
        key = _cstring(name)
        if b"=" in key:
            raise ValueError("illegal environment variable name")
        environment.append(key + b"=" + _cstring(value))
    return ((ctypes.c_char_p * (len(arguments) + 1))(*arguments, None),
            (ctypes.c_char_p * (len(environment) + 1))(*environment, None))


def _duplicate(fd: int) -> int:
    import fcntl
    # Stdio may be closed in a headless parent. Never use 0/1/2 as an action source.
    return fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3)


def _null_input() -> int:
    raw = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
    copied = None
    try:
        copied = _duplicate(raw)
        return copied
    finally:
        try:
            os.close(raw)
        except BaseException:
            # The caller cannot own the duplicate until this helper returns.
            if copied is not None:
                with contextlib.suppress(OSError):
                    os.close(copied)
            raise


def _signal_defaults(api, attributes):
    # Darwin signal.h defines sigset_t as uint32 and __sigbits(n) as 1U << (n-1).
    defaults = ctypes.c_uint32()
    for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
        number = getattr(signal, name, None)
        if number is not None:
            defaults.value |= 1 << (int(number) - 1)
    _check(api.posix_spawnattr_setsigdefault(ctypes.byref(attributes), ctypes.byref(defaults)))
    _check(api.posix_spawnattr_setflags(ctypes.byref(attributes), _SETSID | _CLOEXEC_DEFAULT | _SETSIGDEF))


def _stdio_actions(api, actions, input_fd, output_fd):
    for source, target in ((input_fd, 0), (output_fd, 1), (output_fd, 2)):
        _check(api.posix_spawn_file_actions_adddup2(ctypes.byref(actions), source, target))
    for fd in (input_fd, output_fd):
        _check(api.posix_spawn_file_actions_addclose(ctypes.byref(actions), fd))


def spawn_detached(argv: Sequence[str], *, cwd: str | None, env: Mapping[str, str], log_fd: int) -> int:
    """Launch with null stdin, combined log, own session and no extra inherited fds.

    Cwd remains path-based, as Popen's child chdir is. Native failure reports errno
    synchronously; the API cannot identify which file action/exec pathname failed.
    It never falls back to fork. The caller owns PID custody and waitpid reaping.
    """
    if os_family() != "darwin":
        raise NotImplementedError("Darwin spawn is unavailable on this host")
    arguments, environment = _vectors(argv, env)
    directory = _cstring(cwd) if cwd is not None else None
    sys.audit("os.posix_spawn", argv[0], list(argv), env)
    api = _native_api()
    actions, attributes = ctypes.c_void_p(), ctypes.c_void_p()
    actions_ready = attributes_ready = False
    owned = []
    try:
        output_fd = _duplicate(log_fd)
        owned.append(output_fd)
        input_fd = _null_input()
        owned.append(input_fd)
        _check(api.posix_spawn_file_actions_init(ctypes.byref(actions)))
        actions_ready = True
        _check(api.posix_spawnattr_init(ctypes.byref(attributes)))
        attributes_ready = True
        if directory is not None:
            _check(api.posix_spawn_file_actions_addchdir(ctypes.byref(actions), directory))
        _stdio_actions(api, actions, input_fd, output_fd)
        _signal_defaults(api, attributes)
        pid = ctypes.c_int()
        _check(api.posix_spawn(ctypes.byref(pid), arguments[0], ctypes.byref(actions),
                               ctypes.byref(attributes), arguments, environment))
        return pid.value
    finally:
        # Cleanup must not replace an exec error or lose a successfully created PID.
        # Destroy returns an errno (not an exception); valid initialized handles are ours.
        if attributes_ready:
            api.posix_spawnattr_destroy(ctypes.byref(attributes))
        if actions_ready:
            api.posix_spawn_file_actions_destroy(ctypes.byref(actions))
        for fd in owned:
            with contextlib.suppress(OSError):
                os.close(fd)
