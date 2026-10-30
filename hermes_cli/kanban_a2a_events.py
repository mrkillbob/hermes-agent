"""Foreground event return and explicit one-shot recovery, never periodic reads."""
from contextlib import closing
from dataclasses import asdict
import json
import re

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_coordinator_executor import bounded_connection, event as journal_event, validate_binding
from copy import deepcopy

from hermes_state_a2a import digest
from plugins.platforms.a2a import protocol
from plugins.platforms.a2a.event_return import ACK_SCHEMA, DELIVERY, EVENT_MODE, LOOKUP_SCHEMA

_PREFIX = 'external_a2a_'


def _task(frame):
    value = frame['result']
    if not isinstance(value, dict) or set(value) != {'task'} or not isinstance(value['task'], dict):
        raise ValueError('Full bound task event required')
    task = deepcopy(value['task'])
    meta = task.pop('metadata', {})
    if (not isinstance(meta, dict) or meta.get(EVENT_MODE) != {'ack_schema': ACK_SCHEMA}
            or set(meta) - {EVENT_MODE, DELIVERY}):
        raise ValueError('Negotiated application delivery ACK required')
    event = meta.get(DELIVERY)
    if event is not None and (not isinstance(event, dict) or set(event) != {'id', 'sha256', 'acknowledged'}
            or type(event['acknowledged']) is not bool
            or event['sha256'] != digest(task) or event['id'] != 'event-' + event['sha256']):
        raise ValueError('Conflicting delivery event')
    return task, event


def routed(executor, params):
    return {**params, **({'tenant': executor.plan.tenant} if executor.plan.tenant else {})}


def acknowledge(executor, delivery, event):
    remote = executor._remote(delivery)
    data = {'schema': ACK_SCHEMA, 'task_id': remote['task_id'], 'event_id': event['id'], 'sha256': event['sha256']}
    message = {'role': protocol.ROLE_USER, 'messageId': event['id'], 'parts': [{'data': data}]}
    response = executor.client.rpc('SendMessage', routed(executor, {'message': message}), delivery.key + ':ack')
    parts = ((response.get('result') or {}).get('message') or {}).get('parts')
    if parts != [{'data': {'acknowledged': event['id'], 'sha256': event['sha256']}}]:
        raise ValueError('Unbound delivery acknowledgment')
    delivery_mark(executor, delivery, _PREFIX + 'delivery_ack', event)


def consume(executor, delivery, method, params):
    seen = False
    with closing(executor.client.stream(method, routed(executor, params), delivery.key + ':stream')) as frames:
        for frame in frames:
            task, event = _task(frame)
            # The existing ingestion validates peer task/context, typed result and correlation.
            executor._ingest(delivery, {'result': {'task': task}}, 'SendMessage')
            seen = True
            if event is not None:
                return receive_event(executor, delivery, task, event)
    raise ValueError('Stream closed without a delivery event' if seen else 'Stream closed before task ACK')


def receive_event(executor, delivery, task, event):
    # A cached terminal receipt makes _ingest idempotent, so a later Task with
    # the same peer ID/context may be ignored there. Bind transport ACKs to the
    # exact owner observation that backs the saved result/question.
    from hermes_cli.kanban_a2a_executor import _task as peer_task
    observed = executor._latest(delivery)
    if (observed is None or observed['response'] is None
            or peer_task(observed['response'], observed['method']) != task):
        raise ValueError('Delivery event differs from the saved peer observation')
    status = executor.status(delivery)
    saved = executor.lookup(delivery)
    if (saved is None or saved.kind not in {'completed', 'error'}) and (
            status is None or status.state != protocol.STATE_INPUT_REQUIRED or not status.questions):
        raise ValueError('Delivery requires a validated saved result/question')
    metadata = {k: event[k] for k in ('id', 'sha256')}
    delivery_mark(executor, delivery, _PREFIX + 'delivery', metadata)
    if event['acknowledged']:
        delivery_mark(executor, delivery, _PREFIX + 'delivery_ack', metadata)
    else:
        acknowledge(executor, delivery, metadata)
    return saved


def recover_once(executor, delivery, recovery_id):
    """Explicit caller action after a known disconnect; never called by dispatch ticks."""
    if not isinstance(recovery_id, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', recovery_id) is None:
        raise ValueError('Explicit bounded recovery identity required')
    executor._intent(delivery)
    if executor.journal.read(delivery, _PREFIX + 'delivery_ack') is not None:
        return executor.lookup(delivery)
    if not delivery_mark(executor, delivery, _PREFIX + 'event_recovery:' + recovery_id,
                         {'key': delivery.key, 'recovery_id': recovery_id}, exclusive=True):
        return executor.lookup(delivery)
    event = executor.journal.read(delivery, _PREFIX + 'delivery')
    if event is not None:
        acknowledge(executor, delivery, {k: event[k] for k in ('id', 'sha256')})
        return executor.lookup(delivery)
    remote = executor._remote(delivery)
    if remote is None:
        from hermes_cli.kanban_a2a_executor import _params
        params = _params(delivery, executor.plan)
        text = protocol.extract_text(params)
        fingerprint = digest([{'context_id': protocol.extract_context_id(params), 'text': text}, json.loads(text)['correlation']])
        found = executor.client.rpc('GetTask', routed(executor, {'metadata': {LOOKUP_SCHEMA: {
            'message_id': delivery.key, 'request_sha256': fingerprint}}}), delivery.key + ':recover')
        task, event = _task({'result': {'task': found['result']}})
        executor._ingest(delivery, {'result': {'task': task}}, 'SendMessage')
        if event is not None:
            return receive_event(executor, delivery, task, event)
        remote = executor._remote(delivery)
    return consume(executor, delivery, 'SubscribeToTask', {'id': remote['task_id']})


def delivery_mark(executor, delivery, kind, payload, *, exclusive=False):
    """Only transport receipt metadata may settle after the local terminal task.

    No permission to submit/continue work or change a task lifecycle is added.
    An unsettled delivery still requires the current owned run.
    """
    recovering = kind.startswith(_PREFIX + 'event_recovery:')
    if kind not in {_PREFIX + 'delivery', _PREFIX + 'delivery_ack'} and not recovering:
        raise ValueError('Delivery metadata only')
    journal = executor.journal
    with bounded_connection(journal.database, journal.limits.submit_seconds, writable=True) as conn:
        validate_binding(conn, delivery, journal.database, journal.coordinator_id)
        value = {'binding': digest(asdict(delivery)), **payload}
        old = journal_event(conn, delivery, kind)
        if old is not None:
            if not exclusive and old != value:
                raise ValueError('Conflicting delivery metadata')
            return False
        if recovering:
            prefix = _PREFIX + 'event_recovery:'
            count = conn.execute('SELECT COUNT(*) FROM task_events WHERE task_id=? AND run_id=? AND substr(kind,1,?)=?',
                                 (delivery.task_id, delivery.run_id, len(prefix), prefix)).fetchone()[0]
            if count >= 3:
                raise ValueError('Recovery limit reached; owner intervention required')
        # A saved typed result/question is the sole exception to running ownership here.
        terminal = journal_event(conn, delivery, _PREFIX + 'terminal')
        frozen = terminal['envelope'] if terminal else journal_event(conn, delivery, _PREFIX + 'attention')
        if frozen is None:
            validate_binding(conn, delivery, journal.database, journal.coordinator_id, owned=True)
        else:
            from hermes_cli.kanban_external import artifact_from_envelope
            if artifact_from_envelope(frozen).delivery != delivery:
                raise ValueError('Foreign terminal receipt')
        kb._append_event(conn, delivery.task_id, kind, value, run_id=delivery.run_id)
        return True
