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
from pm.lifetime_handles import (
    directory_identities, directory_identity, file_identities, file_identity, native_reference, open_hold,
)

_ACTIVE = ContextVar("pm_lifetime_activities", default=())


class LifetimeUnavailable(RuntimeError):
    """Required ownership coordination could not be established."""


def _stable_id(value):
    if (not isinstance(value, list) or len(value) != 2
            or type(value[1]) is not int or value[1] <= 0 or not isinstance(value[0], str)):
        return False
    try:
        parsed = uuid.UUID(value[0])
        return bool(parsed.int) and str(parsed) == value[0]
    except ValueError:
        return False


def _identity(path, *, legacy=False, stable=False):
    if stable:
        from hermes_platform.host.volume_identity import local_directory_identity
        return local_directory_identity(path)
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
    def __init__(self, domain, *, stable_identity=False):
        self.domain = Path(domain).resolve(strict=False)
        self.namespace = self.domain.parent / ".lifetimes" / self.domain.name
        self.activities = self.namespace / "activities"
        self.marker = self.namespace / "domain.json"
        self.fd = None
        self.stable = stable_identity
        self.require_stable = stable_identity
        self.namespace_created = False

    def bind_protocol(self):
        """Select once under the held kernel gate; unknown records never downgrade."""
        previous = self._record()
        if previous is None:
            return
        if not isinstance(previous, dict) or type(previous.get('version')) is not int:
            raise LifetimeUnavailable('unknown lifetime identity protocol')
        keys = {'version', 'path', 'platform', 'activities', 'gate'}
        keys.add('retired' if previous.get('retired') is True else 'domain')
        if previous['version'] == 2 and previous.get('identity') == 'darwin_local_volume_uuid':
            keys.add('identity')
            stable = True
        elif previous['version'] == 1:
            stable = False
        else:
            raise LifetimeUnavailable('unknown lifetime identity protocol')
        if set(previous) != keys:
            raise LifetimeUnavailable('unknown lifetime identity protocol')
        if stable and (previous['path'] != _recorded_path(self.domain)
                       or type(previous['platform']) is not str or previous['platform'] != os.name
                       or any(not _stable_id(previous[key]) for key in
                              ('activities', 'gate', *(() if 'retired' in previous else ('domain',))))):
            raise LifetimeUnavailable('unknown lifetime identity protocol')
        self.stable = stable

    def _stable_gate_identity(self):
        from hermes_platform.host.volume_identity import local_file_identity, _object_version
        path = self.namespace / 'gate'
        before = os.fstat(self.fd)
        if _object_version(path.lstat()) != _object_version(before):
            raise LifetimeUnavailable('lifetime gate identity changed')
        identity = local_file_identity(self.fd)
        if any(_object_version(info) != _object_version(before)
               for info in (os.fstat(self.fd), path.lstat())):
            raise LifetimeUnavailable('lifetime gate identity changed')
        return identity

    def _base_snapshot(self, *, legacy=False):
        if self.stable:
            return {'version': 2, 'identity': 'darwin_local_volume_uuid',
                    'path': _recorded_path(self.domain), 'platform': os.name,
                    'activities': _identity(self.activities, stable=True),
                    'gate': self._stable_gate_identity()}
        return {"version": 1, "path": _recorded_path(self.domain), "platform": os.name,
                "activities": _identity(self.activities, legacy=legacy),
                "gate": file_identity(self.fd, legacy=legacy)}

    def _snapshot(self, *, legacy=False):
        return {**self._base_snapshot(legacy=legacy), "domain": _identity(self.domain, legacy=legacy, stable=self.stable)}

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
        if self.require_stable and not self.stable:
            raise LifetimeUnavailable('stable lifetime identity requires new qualified state')
        if self.stable and previous is None and (self.domain.exists() or not self.namespace_created):
            raise LifetimeUnavailable('stable lifetime identity requires new qualified state')
        if self.stable and previous is None and any(path.name != 'gate' for path in self.namespace.iterdir()):
            raise LifetimeUnavailable('stable lifetime identity requires new qualified state')
        if not self.domain.exists():
            if not create:
                raise LifetimeUnavailable("managed environment no longer exists")
            retired = self._verified_snapshot(previous, retired=True) if previous is not None else None
            if previous is not None and retired is None:
                raise LifetimeUnavailable("missing managed state has no confirmed retirement")
            self.domain.mkdir(parents=True)
            self.activities.mkdir(exist_ok=True)
            created = self._snapshot()
            recreated_base = dict(created, retired=True)
            recreated_base.pop("domain")
            if retired is not None and recreated_base != retired:
                raise LifetimeUnavailable("managed environment identity changed")
            durable_write_bytes(self.marker, json.dumps(created).encode())
        elif previous is not None:
            verified = self._verified_snapshot(previous)
            if verified is None:
                raise LifetimeUnavailable("managed environment identity changed")
            if previous != verified:
                durable_write_bytes(self.marker, json.dumps(verified).encode())
                if not self.qualified:
                    raise LifetimeUnavailable("managed environment identity changed")
        else:
            _identity(self.domain)
            self.activities.mkdir(exist_ok=True)
        _identity(self.activities)

    def _verified_snapshot(self, record, *, retired=False):
        try:
            return self._inspect_snapshot(record, retired=retired)
        except (OSError, ValueError, TypeError, LifetimeUnavailable):
            return None

    def _inspect_snapshot(self, record, *, retired):
        if self.stable:
            snapshot = self._base_snapshot() | ({'retired': True} if retired else
                                               {'domain': _identity(self.domain, stable=True)})
            return snapshot if record == snapshot else None
        base = {"version": 1, "path": _recorded_path(self.domain), "platform": os.name}
        activities = directory_identities(self.activities)
        locked = file_identities(self.fd)
        domains = (None, None) if retired else directory_identities(self.domain)
        snapshots = [{**base, "activities": activities[index], "gate": locked[index],
                      **({"retired": True} if retired else {"domain": domains[index]})}
                     for index in range(2 if os.name == "nt" else 1)]
        # A failed historical update can leave a 3.11 marker. Verify its entire
        # known snapshot under the gate; both representations come from the same
        # handle per object. Publish these captured IDs, never a replacement's.
        return snapshots[0] if record in snapshots else None

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
                    keys = {'version', 'domain', 'platform', 'generation'} | ({'identity'} if self.stable else set())
                    if (set(data) != keys or type(data.get('version')) is not int
                            or data['version'] != (2 if self.stable else 1)
                            or (self.stable and data.get('identity') != 'darwin_local_volume_uuid')
                            or (self.stable and not _stable_id(data.get('domain')))
                            or not self._activity_domain_matches(data["domain"])
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
        if self.stable:
            return identity == _identity(self.domain, stable=True)
        return (identity == _identity(self.domain) or
                (os.name == "nt" and identity == _identity(self.domain, legacy=True)))


@contextmanager
def gate(domain, *, wait=True, deadline=None, stable_identity=False):
    guard = Gate(domain, stable_identity=stable_identity)
    root = guard.namespace.parent
    root.mkdir(parents=True, exist_ok=True)
    _identity(root)
    try:
        guard.namespace.mkdir()
        guard.namespace_created = True
    except FileExistsError:
        pass
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
        guard.bind_protocol()
        yield guard
    finally:
        os.close(fd)


class Activity:
    def __init__(self, guard, generation):
        self.domain = guard.domain
        self.path = guard.activities / uuid.uuid4().hex
        self.failed_outputs = {}
        self.domain_identity = _identity(self.domain, stable=guard.stable)
        self.coordination = guard._base_snapshot()
        if generation is not None:
            try:
                _identity(Path(generation))
            except OSError as exc:
                raise LifetimeUnavailable("dependency generation no longer exists") from exc
        scope = _generation_scope(generation, self.domain) if generation else None
        data = {'version': 2 if guard.stable else 1, 'domain': self.domain_identity,
                'generation': scope, 'platform': os.name}
        if guard.stable:
            data['identity'] = 'darwin_local_volume_uuid'
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


def acquire_activity(domain, *, generation=None, create=False, deadline=None, stable_identity=False):
    with gate(domain, deadline=deadline, stable_identity=stable_identity) as guard:
        if guard is None:
            raise LifetimeUnavailable("timed out acquiring environment lifetime protection")
        guard.prepare(create)
        return Activity(guard, generation)


@contextmanager
def activity(domain, *, generation=None, create=False, deadline=None, stable_identity=False):
    owned = acquire_activity(domain, generation=generation, create=create, deadline=deadline,
                             stable_identity=stable_identity)
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
