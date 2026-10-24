"""Stable outside-state gates and kernel-owned activities for managed environments.

A protocol marker qualifies only a freshly created domain. Existing state is not
silently certified. Registration and collection share a gate outside the domain;
activity references, rather than a long exclusive gate, protect live users.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager, suppress, ExitStack
from contextvars import ContextVar
import json
import os
import re
from pathlib import Path
import stat
import time
import uuid

from pm.filesystem import durable_write_bytes, lock_fd, native
from pm.lifetime_handles import directory_identity, file_identity, native_reference, open_hold

_ACTIVE = ContextVar("pm_lifetime_activities", default=())


class LifetimeUnavailable(RuntimeError):
    """Required ownership coordination could not be established."""


def _identity(path, *, legacy=False):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise LifetimeUnavailable("lifetime path is not a concrete directory")
    if os.name == "nt":
        try:
            return directory_identity(path, legacy=legacy)
        except ValueError as exc:
            raise LifetimeUnavailable(str(exc)) from exc
    return [info.st_dev, info.st_ino]


def _recorded_path(path):
    return os.path.normcase(native(path)) if os.name == "nt" else str(path)


def _generation_scope(generation, domain):
    generation = Path(generation).resolve()
    if os.name == "nt":
        generation, domain = Path(native(generation)), Path(native(domain))
    return str(generation.relative_to(domain))


def _regular(path):
    return stat.S_ISREG(path.lstat().st_mode)


def domain_for_generation(generation: Path) -> Path:
    generation = Path(generation).absolute()
    parent = generation.parent
    if parent.name == "environments":
        return parent.parent
    if parent.name == "generations":
        runtime = parent.parent
        return runtime.parent if runtime.name == "pm-runtime" and runtime.parent.parent.name == "installs" else runtime
    if parent.name == "test-environment":
        return parent.parent if parent.parent.parent.name == "installs" else parent
    return parent


class Gate:
    def __init__(self, domain):
        self.domain = Path(domain).resolve(strict=False)
        self.namespace = self.domain.parent / ".lifetimes" / self.domain.name
        self.activities = self.namespace / "activities"
        self.marker = self.namespace / "domain.json"
        self.fd = None

    def _base_snapshot(self, *, legacy=False):
        return {"version": 1, "path": _recorded_path(self.domain), "platform": os.name,
                "activities": _identity(self.activities, legacy=legacy),
                "gate": file_identity(self.fd, legacy=legacy)}

    def _snapshot(self, *, legacy=False):
        return {**self._base_snapshot(legacy=legacy), "domain": _identity(self.domain, legacy=legacy)}

    def _record(self):
        try:
            if not _regular(self.marker):
                raise LifetimeUnavailable("lifetime qualification is not a regular file")
            record = json.loads(self.marker.read_text())
            if isinstance(record, dict) and isinstance(record.get("path"), str):
                record["path"] = _recorded_path(record["path"])
            return record
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise LifetimeUnavailable("cannot inspect lifetime qualification") from exc

    @property
    def qualified(self):
        try:
            return self._record() == self._snapshot()
        except (OSError, ValueError, TypeError, LifetimeUnavailable):
            return False

    def prepare(self, create):
        previous = self._record()
        if not self.domain.exists():
            if not create:
                raise LifetimeUnavailable("managed environment no longer exists")
            if previous is not None and not self._matches(previous, retired=True):
                raise LifetimeUnavailable("missing managed state has no confirmed retirement")
            self.domain.mkdir(parents=True)
            self.activities.mkdir(exist_ok=True)
            durable_write_bytes(self.marker, json.dumps(self._snapshot()).encode())
        elif previous is not None:
            if not self._matches(previous):
                raise LifetimeUnavailable("managed environment identity changed")
            if not self.qualified:
                durable_write_bytes(self.marker, json.dumps(self._snapshot()).encode())
        else:
            _identity(self.domain)
            self.activities.mkdir(exist_ok=True)
        _identity(self.activities)

    def _matches(self, record, *, retired=False):
        snapshot = self._base_snapshot if retired else self._snapshot
        suffix = {"retired": True} if retired else {}
        if record == {**snapshot(), **suffix}:
            return True
        # A failed historical update can leave a 3.11 marker. Verify its entire
        # known native snapshot under the gate before upgrading the protocol.
        return os.name == "nt" and record == {**snapshot(legacy=True), **suffix}

    def confirm_removed(self, ownership):
        if self.domain.exists() or self._record() != ownership or ownership != self._base_snapshot() | {"domain": ownership["domain"]}:
            raise LifetimeUnavailable("cannot confirm domain retirement")
        durable_write_bytes(self.marker, json.dumps({**self._base_snapshot(), "retired": True}).encode())

    def held(self, *, generation=None, writers_only=False):
        """Probe under this gate; only its exact target and whole-domain writers pin a generation."""
        try:
            entries = list(self.activities.iterdir())
            target = _generation_scope(generation, self.domain) if generation else None
            for entry in entries:
                if not _regular(entry):
                    return True
                if not re.fullmatch(r"[0-9a-f]{32}", entry.name):
                    return True
                try:
                    data = json.loads(entry.read_text())
                    if (set(data) != {"version", "domain", "platform", "generation"}
                            or data["version"] != 1 or not self._activity_domain_matches(data["domain"])
                            or data["platform"] != os.name):
                        return True
                    scope = data["generation"]
                    if scope is not None and (not isinstance(scope, str) or ".." in Path(scope).parts
                                              or Path(scope).is_absolute()):
                        return True
                except (OSError, ValueError, KeyError, TypeError):
                    return True
                fd = open_hold(entry)
                if fd is not None:
                    os.close(fd)
                    entry.unlink()
                    continue
                related = (target is None or target == scope or (scope is not None and
                           (Path(target).is_relative_to(scope) or Path(scope).is_relative_to(target))))
                if scope is None or (not writers_only and related):
                    return True
            return False
        except (OSError, ValueError, LifetimeUnavailable):
            return True

    def _activity_domain_matches(self, identity):
        return (identity == _identity(self.domain) or
                (os.name == "nt" and identity == _identity(self.domain, legacy=True)))


@contextmanager
def gate(domain, *, wait=True, deadline=None):
    guard = Gate(domain)
    root = guard.namespace.parent
    root.mkdir(parents=True, exist_ok=True)
    _identity(root)
    guard.namespace.mkdir(exist_ok=True)
    _identity(guard.namespace)
    path = guard.namespace / "gate"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        if not lock_fd(fd, wait=wait, timeout=remaining):
            yield None
            return
        opened, named = os.fstat(fd), path.lstat()
        if not stat.S_ISREG(named.st_mode) or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise LifetimeUnavailable("lifetime gate identity changed")
        guard.fd = fd
        yield guard
    finally:
        os.close(fd)


class Activity:
    def __init__(self, guard, generation):
        self.domain = guard.domain
        self.path = guard.activities / uuid.uuid4().hex
        self.failed_outputs = {}
        self.domain_identity = _identity(self.domain)
        self.coordination = guard._base_snapshot()
        if generation is not None:
            try:
                _identity(Path(generation))
            except OSError as exc:
                raise LifetimeUnavailable("dependency generation no longer exists") from exc
        scope = _generation_scope(generation, self.domain) if generation else None
        data = {"version": 1, "domain": _identity(self.domain), "generation": scope, "platform": os.name}
        self.data = json.dumps(data).encode()
        durable_write_bytes(self.path, self.data)
        self.fd = open_hold(self.path, reader=True)
        if self.fd is None:
            raise LifetimeUnavailable("cannot register managed environment activity")
        info = os.fstat(self.fd)
        self.identity = (info.st_dev, info.st_ino)
        atexit.register(self.close)

    @property
    def reference(self):
        if self.fd is None:
            raise LifetimeUnavailable("activity reference is closed")
        return native_reference(self.fd)

    def close(self):
        if self.fd is None:
            return
        atexit.unregister(self.close)
        fd, self.fd = self.fd, None
        os.close(fd)  # no explicit unlock: an inherited child may still own the reference
        try:
            with gate(self.domain, wait=False) as guard:
                if guard is None:
                    return
                probe = open_hold(self.path) if self.path.exists() else None
                if probe is not None:
                    info = os.fstat(probe)
                    os.close(probe)
                    if (info.st_dev, info.st_ino) == self.identity and self.path.read_bytes() == self.data:
                        self.path.unlink()
                if self.failed_outputs:
                    from pm.lifetime_cleanup import finish_failed_outputs
                    finish_failed_outputs(self, guard)
        except (OSError, ValueError, LifetimeUnavailable):
            pass  # stale unlocked records are pruned under the next gate


def acquire_activity(domain, *, generation=None, create=False, deadline=None):
    with gate(domain, deadline=deadline) as guard:
        if guard is None:
            raise LifetimeUnavailable("timed out acquiring environment lifetime protection")
        guard.prepare(create)
        return Activity(guard, generation)


@contextmanager
def activity(domain, *, generation=None, create=False, deadline=None):
    owned = acquire_activity(domain, generation=generation, create=create, deadline=deadline)
    token = _ACTIVE.set((*_ACTIVE.get(), owned))
    try:
        yield owned
    finally:
        _ACTIVE.reset(token)
        owned.close()


def active_references():
    return tuple(owned.reference for owned in _ACTIVE.get() if owned.fd is not None)


def side_domain(root):
    root = Path(root).absolute()
    if root.name in {"test-environment", "pm-runtime"} and root.parent.parent.name == "installs":
        return root.parent
    return root


@contextmanager
def collection_gate(domain):
    """Maintenance retains inaccessible/replaced coordination state, rather than failing sync."""
    with ExitStack() as stack:
        try:
            guard = stack.enter_context(gate(domain, wait=False))
        except (OSError, LifetimeUnavailable):
            guard = None
        yield guard
