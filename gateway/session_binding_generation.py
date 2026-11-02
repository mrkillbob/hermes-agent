"""Local publication ordering for the native binding integrator; no authority issuance.

Currently unimported by runtime code. The integrator must supply a fresh owner-issued
incarnation per registry lifetime and register every relevant writer. Scope values
come from the existing authenticated Principal and owned live session, never a grant
supplied by a renderer. An observation is not a credential, lease or signed receipt.

Contexts are synchronous owner-loop sections: never await inside one. This local
ordering cannot hold remote revocation/expiry through receiver commit. The receiver
needs its own qualified commit fence; checking this helper before sending is insufficient.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
import re
from threading import get_ident


MAX_REVISION = (1 << 64) - 1
_INCARNATION = re.compile(r'[0-9a-f]{32}')


class GenerationUnavailable(RuntimeError):
    """Fixed local refusal; does not contain identity or private metadata."""

    def __init__(self):
        super().__init__('binding_generation_unavailable')


def _refuse():
    raise GenerationUnavailable()


@dataclass(frozen=True)
class BindingScope:
    """Exact already-authenticated scope; construction does not authenticate it."""
    actor_subject: str
    actor_transport_id: str
    profile_id: str
    session_id: str

    def __post_init__(self):
        values = (self.actor_subject, self.actor_transport_id, self.profile_id, self.session_id)
        if any(type(value) is not str or not value or len(value) > 4096 for value in values):
            _refuse()


@dataclass(eq=False)
class _Slot:
    authority: object
    revision: int
    context: tuple
    busy: object = None
    failed: bool = False
    retired: set = field(default_factory=set)


@dataclass(frozen=True)
class Prepared:
    owner: object
    key: str
    slot: object
    authority: object
    revision: int
    context: tuple


@dataclass(frozen=True)
class Observation:
    """In-process witness. Do not serialize the private preparation/authority."""
    prepared: Prepared
    scope: BindingScope
    metadata_incarnation: str
    metadata_revision: str


class BindingGenerations:
    def __init__(self, incarnation: str):
        if type(incarnation) is not str or not _INCARNATION.fullmatch(incarnation):
            _refuse()
        self.incarnation = incarnation
        self.thread = get_ident()
        self.loop = None
        self.slots = {}

    def check_owner(self):
        if get_ident() != self.thread:
            _refuse()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _refuse()
        if self.loop is None:
            self.loop = loop
        elif self.loop is not loop:
            _refuse()

    @contextmanager
    def replacement(self, key, authority):
        """Enclose complete registry bookkeeping; partial failure stays unavailable."""
        if get_ident() != self.thread:
            _refuse()
        if self.loop is not None:
            self.check_owner()
        existing = getattr(authority, '_binding_generation', None)
        if existing is not None and existing[2].busy is not None:
            _refuse()
        old = self.slots.get(key)
        if old is not None and old.busy is not None:
            _refuse()
        revision = old.revision + 1 if old is not None else 1
        if revision >= MAX_REVISION:
            if old is not None:
                old.failed = True
            _refuse()
        slot = self.slots[key] = _Slot(authority, revision,
            _context(authority) if authority is not None else None, busy=object())
        try:
            if authority is not None:
                authority._binding_generation = (self, key, slot)
            yield
        except BaseException:
            slot.failed = True
            raise
        finally:
            slot.busy = None
            slot.revision += 1


def _context(authority):
    return authority.profile_id, authority.instance_id, authority.epoch


def prepare(authority):
    binding = getattr(authority, '_binding_generation', None)
    if binding is None:
        _refuse()
    owner, key, slot = binding
    owner.check_owner()
    if (owner.slots.get(key) is not slot or slot.authority is not authority
            or slot.busy is not None or slot.failed or slot.context != _context(authority)
            or getattr(authority, 'retiring', False) or getattr(authority, 'retired', False)):
        _refuse()
    return Prepared(owner, key, slot, authority, slot.revision, _context(authority))


def validate(token, authority, targets=()):
    token.owner.check_owner()
    slot = token.slot
    if (getattr(authority, '_binding_generation', None) != (token.owner, token.key, slot)
            or token.authority is not authority or token.owner.slots.get(token.key) is not slot
            or slot.authority is not authority or slot.revision != token.revision
            or slot.busy is not None or slot.failed or token.context != _context(authority)
            or slot.retired.intersection(targets)
            or getattr(authority, 'retiring', False) or getattr(authority, 'retired', False)):
        _refuse()


@contextmanager
def publication(authority, token=None, *, targets=(), parent=None):
    """Prepare before asynchronous work, then validate its token before publication."""
    if parent is not None:
        token, permit = parent
        token.owner.check_owner()
        if (token.authority is not authority or token.owner.slots.get(token.key) is not token.slot
                or token.slot.busy is not permit or token.slot.retired.intersection(targets)
                or getattr(authority, '_binding_generation', None) != (token.owner, token.key, token.slot)
                or token.slot.failed or token.context != _context(authority)
                or getattr(authority, 'retiring', False) or getattr(authority, 'retired', False)):
            _refuse()
        try:
            yield parent
        except BaseException:
            token.slot.failed = True
            raise
        return
    token = prepare(authority) if token is None else token
    validate(token, authority, targets)
    slot, permit = token.slot, object()
    if slot.revision > MAX_REVISION - 2:
        slot.failed = True
        _refuse()
    slot.revision += 1
    slot.busy = permit
    try:
        yield (token, permit)
    except BaseException:
        slot.failed = True
        raise
    finally:
        slot.busy = None
        slot.revision += 1


def observe(authority, scope: BindingScope) -> Observation:
    if type(scope) is not BindingScope or scope.profile_id != authority.profile_id:
        _refuse()
    token = prepare(authority)
    validate(token, authority, (scope.session_id,))
    return Observation(token, scope, token.owner.incarnation, str(token.revision))


def validate_observation(observation: Observation, authority, scope: BindingScope):
    """Local equality/freshness only; receiver commit still needs its authority fence."""
    if (type(observation) is not Observation or type(scope) is not BindingScope
            or observation.scope != scope or scope.profile_id != authority.profile_id
            or observation.metadata_incarnation != observation.prepared.owner.incarnation
            or observation.metadata_revision != str(observation.prepared.revision)):
        _refuse()
    validate(observation.prepared, authority, (scope.session_id,))
