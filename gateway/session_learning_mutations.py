"""Only participating local reference-owner transitions, not a global writer fence.

Short same-process mutation/commit serialization; no network wait on the owner
loop. In-flight bytes cannot be recalled: invalidation prevents new wire entry
and successful settlement; explicit revoke/close joins physical-worker cleanup.
"""
from contextlib import contextmanager, ExitStack

from gateway.session_learning_execution import ReferenceExecution, _deny
from gateway.session_learning_cache import outstanding_work


@contextmanager
def reference_change(authority, session_ids, *, actor=None, transport=None):
    selected = [e for e in getattr(authority, '_reference_intents', {}).values()
                if type(e) is ReferenceExecution and e.ref.session_id in session_ids
                and (e.status not in {'revoked', 'settled', 'unknown'} or outstanding_work(e))
                and (actor is None or e.selected.connection.actor is actor)
                and (transport is None or e.selected.connection.transport is transport)]
    brokers = sorted({id(e.broker): e.broker for e in selected}.values(), key=id)
    with ExitStack() as stack:
        for broker in brokers:
            if not broker._gate.acquire(blocking=False):
                _deny('reference_change_busy')
            stack.callback(broker._gate.release)
        for execution in selected:
            permit = execution.selected.admission
            gates = ([permit.gate] if permit is not None else []) + [execution.worker_gate]
            for gate in gates:
                if not gate.acquire(blocking=False):
                    _deny('reference_change_busy')
                stack.callback(gate.release)
        for execution in selected:
            execution.status = 'revoked'
            execution.selected.reference = None
    # Permanent invalidation is the linearization point. Release before caller
    # cleanup can touch SQLite/spawn a release worker; no lock-order inversion.
    from gateway.session_learning_cache import release_pin
    for execution in selected:
        release_pin(execution)
    yield


async def retire_membership(authority, session_id, actor):
    selected = [e for e in getattr(authority, '_reference_intents', {}).values()
                if type(e) is ReferenceExecution and e.ref.session_id == session_id
                and e.selected.connection.actor is actor and e.status != 'settled']
    for execution in selected:
        s = execution.selected
        with execution.broker._gate:
            execution.status, s.status, s.reference = 'revoked', 'revoked', None
    for execution in selected:
        s = execution.selected
        if s.admission is not None:
            await s.admission.quiescent()
        if execution.native_send is not None:
            await execution.native_send.issuer.quiescent()
        from gateway.session_learning_cache import release_pin
        release_pin(execution)


def route_change(authority, route, session_id=None):
    ids = {sid for sid, live in authority.sessions.items()
           if live.route == route and sid != session_id}
    if session_id in authority.sessions and authority.sessions[session_id].route != route:
        ids.add(session_id)
    return reference_change(authority, ids)


@contextmanager
def resident_change(runner, session_key):
    from gateway.session_authorities import all_authorities
    with ExitStack() as stack:
        for authority in all_authorities(runner):
            ids = {e.ref.session_id for e in getattr(authority, '_reference_intents', {}).values()
                   if e.route == session_key}
            stack.enter_context(reference_change(authority, ids))
        yield
