"""Cooperative per-name reservation shared by profile lifecycle and data repair.

Locks live outside the profile collection so rename/delete/recreation keep using
the same inode. They supplement runtime ownership, never replace gateway.lock.
"""
from contextlib import ExitStack, contextmanager
from functools import wraps
from inspect import signature
import os
from pathlib import Path
import threading
import unicodedata

_MUTEXES = {}
_MUTEX_GUARD = threading.Lock()
_LOCAL = threading.local()


def _lock_path(home):
    home = Path(home)
    root = home.parent.resolve().parent
    return root / "runtime" / "profile-lifecycle" / (unicodedata.normalize("NFC", home.name) + ".lock")


@contextmanager
def _reserve_one(home):
    from hermes_cli.active_sessions import _FileLock

    path = _lock_path(home)
    key = os.path.normcase(str(path))
    with _MUTEX_GUARD:
        mutex = _MUTEXES.setdefault(key, threading.RLock())
    with mutex:
        held = getattr(_LOCAL, "held", None)
        if held is None:
            held = _LOCAL.held = set()
        if key in held:
            yield
            return
        with _FileLock(path):
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)


@contextmanager
def profile_lifecycle_reservation(*homes):
    """Reserve sorted profile names, reentrantly within the current thread."""
    with ExitStack() as stack:
        for home in sorted(set(map(Path, homes)), key=lambda p: os.path.normcase(str(_lock_path(p)))):
            stack.enter_context(_reserve_one(home))
        yield


def reserve_profile_operation(*parameters):
    """Apply the shared reservation to existing profile CRUD implementations."""
    def decorate(function):
        call_signature = signature(function)
        @wraps(function)
        def reserved(*args, **kwargs):
            from hermes_cli.profiles import _canon_valid, get_profile_dir

            bound = call_signature.bind(*args, **kwargs)
            first = _canon_valid(bound.arguments[parameters[0]])
            # Renaming default changes its display label, not named-profile identity.
            # Default create/delete keep their existing refusal behavior.
            if first == "default":
                return function(*args, **kwargs)
            names = [first, *(_canon_valid(bound.arguments[name]) for name in parameters[1:])]
            with profile_lifecycle_reservation(*(get_profile_dir(name) for name in names)):
                return function(*args, **kwargs)
        return reserved
    return decorate
