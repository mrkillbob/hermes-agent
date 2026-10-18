"""Opt-in A2A event tasks through the canonical owner; no polling or auto-start."""
from contextlib import asynccontextmanager
from copy import deepcopy
from functools import partial
import asyncio
import json
from pathlib import Path

from gateway.session_contract import SessionRef
from hermes_state_a2a import EventTasks
from hermes_state_runtime import RuntimeStoreError
from plugins.platforms.a2a import protocol, security


def _bounded_result_task(record, body, state):
    """Keep terminal projections inside both durable-store and SSE frame limits."""
    import hermes_state_a2a as a2a_state
    from plugins.platforms.a2a.event_return import receipt_fits

    def build(summary):
        payload = dict(body, summary=summary,
                       questions=[summary] if body['kind'] == 'question' else [])
        task = protocol.build_task(record['task_id'], record['context_id'], state)
        if body['kind'] == 'question':
            task['status']['message'] = protocol.text_message(protocol.ROLE_AGENT, json.dumps(payload), record['context_id'])
        else:
            task['artifacts'] = [{'artifactId': record['task_id'] + ':result',
                                  'parts': [protocol.text_part(json.dumps(payload))]}]
        event = {'id': 'event-' + a2a_state.digest(task), 'sha256': a2a_state.digest(task), 'task': task}
        persisted = dict(record, event=event)
        fits = receipt_fits({'task': task, 'event': event, 'acked': False})
        return task, len(a2a_state.encoded(persisted).encode()) <= a2a_state._MAX_BYTES and fits

    summary = body['summary']
    task, fits = build(summary)
    if fits:
        return task
    marker = ' … [result truncated to fit A2A receipt limits]'
    low, high, best = 0, len(summary), None
    while low <= high:
        middle = (low + high) // 2
        candidate = summary[:middle] + marker
        task, fits = build(candidate)
        if fits:
            best, low = task, middle + 1
        else:
            high = middle - 1
    if best is None:
        raise RuntimeStoreError('invalid_params')
    return best


def assignment(text):
    from plugins.platforms.a2a.client import decode_json
    value = decode_json(text)
    if (not isinstance(value, dict) or set(value) != {'schema', 'expected_result_schema', 'correlation', 'assignment'}
            or value['schema'] != 'hermes.a2a.assignment.v1' or value['expected_result_schema'] != 'hermes.a2a.receipt.v1'
            or not isinstance(value['assignment'], dict) or set(value['assignment']) != {'title', 'body'}
            or any(not isinstance(v, str) for v in value['assignment'].values())):
        raise RuntimeStoreError('invalid_params')
    corr = value['correlation']
    if (not isinstance(corr, dict) or set(corr) != {'task_id', 'run_id', 'key', 'payload_sha256'}
            or type(corr['run_id']) is not int or corr['run_id'] <= 0
            or any(not isinstance(corr[k], str) or not 0 < len(corr[k]) <= 1024
                   for k in ('task_id', 'key', 'payload_sha256'))):
        raise RuntimeStoreError('invalid_params')
    return corr


def result_task(record, receipt, result):
    reply = security.redact_outbound(result.get('final_response') or '')
    kind = 'completed' if receipt.outcome == 'completed' and not result.get('failed') else 'error'
    if kind == 'completed' and reply.lstrip().upper().startswith(protocol.INPUT_REQUIRED_MARKER):
        kind, reply = 'question', reply.lstrip()[len(protocol.INPUT_REQUIRED_MARKER):].strip()
    if kind == 'question' and not reply:
        raise RuntimeStoreError('invalid_params')
    summary = reply or 'Owner execution failed without a result'
    body = {'schema': 'hermes.a2a.receipt.v1', 'correlation': record['correlation'], 'kind': kind,
            'summary': summary, 'findings': [], 'questions': [summary] if kind == 'question' else []}
    state = {'completed': protocol.STATE_COMPLETED, 'error': protocol.STATE_FAILED,
             'question': protocol.STATE_INPUT_REQUIRED}[kind]
    return _bounded_result_task(record, body, state)


def _same_terminal_projection(saved_event, candidate):
    """Match duplicate projections while ignoring only generated timestamp and question ID."""
    import hermes_state_a2a as a2a_state

    if not isinstance(saved_event, dict) or set(saved_event) != {'id', 'sha256', 'task'}:
        return False
    saved = saved_event['task']
    if (not isinstance(saved, dict) or saved_event['sha256'] != a2a_state.digest(saved)
            or saved_event['id'] != 'event-' + saved_event['sha256']):
        return False
    if not isinstance(candidate, dict) or saved.get('id') != candidate.get('id'):
        return False
    state = (candidate.get('status') or {}).get('state')
    if state not in {protocol.STATE_COMPLETED, protocol.STATE_FAILED, protocol.STATE_INPUT_REQUIRED}:
        return False
    left, right = deepcopy(saved), deepcopy(candidate)
    for task in (left, right):
        status = task.get('status')
        if not isinstance(status, dict) or not isinstance(status.get('timestamp'), str):
            return False
        status.pop('timestamp')
        if state == protocol.STATE_INPUT_REQUIRED:
            message = status.get('message')
            if not isinstance(message, dict) or not isinstance(message.get('messageId'), str):
                return False
            message.pop('messageId')
    return left == right


async def rpc(connection, params):
    actor, owner = connection.actor, connection.authority
    required = {'session:create', 'session:submit', 'session:read'}
    if actor.profile_id != owner.profile_id or not required <= actor.capabilities:
        raise RuntimeStoreError('permission_denied')
    common = {'op', 'agent', 'tenant', 'peer'}
    fields = {'accept': {'message_id', 'text', 'context_id'}, 'observe': {'task_id'},
              'ack': {'task_id', 'event_id', 'sha256'}, 'find': {'message_id', 'request_sha256'}}
    op = params.get('op')
    if (op not in fields or set(params) != common | fields[op]
            or any(not isinstance(v, str) for v in params.values())):
        raise RuntimeStoreError('invalid_params')
    tasks = EventTasks(owner.db, owner.epoch, (actor.subject, params['agent'], params['tenant'], params['peer']))
    from gateway.session_runtime_workers import tracked_write
    async def write(fn, *args):
        return await tracked_write(owner, partial(fn, *args))
    if op == 'accept':
        corr = assignment(params['text'])
        rec = await write(tasks.reserve, params['message_id'],
                          {k: params[k] for k in ('context_id', 'text')}, corr)
        if rec['admission'] is None:
            from gateway.session_a2a import forward
            saved = await forward(connection, dict(agent=params['agent'], tenant=params['tenant'], peer=params['peer'],
                context_id=rec['context_id'], input_id=rec['task_id'], text=security.wrap_inbound(params['peer'], params['text'])))
            rec = await write(tasks.bind, rec['task_id'], {k: saved[k] for k in ('session_id', 'admission_id')})
    elif op == 'find':
        rec = await asyncio.to_thread(tasks.find, params['message_id'], params['request_sha256'])
    else:
        rec = await asyncio.to_thread(tasks.read, params['task_id'])
    if op == 'ack':
        rec = await write(tasks.ack, rec['task_id'], params['event_id'], params['sha256'])
        return {'acknowledged': rec['event']['id'], 'sha256': rec['event']['sha256']}
    if rec['admission'] is None:
        # An interrupted admission binding is never guessed or recreated by a read.
        raise RuntimeStoreError('unknown_execution')
    if rec['event'] is not None:
        return {'task': rec['event']['task'], 'event': rec['event'],
                'admission_id': rec['admission']['admission_id'], 'acked': rec['acked']}
    ref = SessionRef(owner.profile_id, rec['admission']['session_id'])
    if ref.session_id not in connection.subscriptions:
        await connection.resume(ref, {})
    receipt = await owner.receipt(actor, ref, rec['admission']['admission_id'])
    if receipt.status == 'unknown':
        raise RuntimeStoreError('unknown_execution')
    if receipt.status == 'terminal' and rec['event'] is None:
        from gateway.session_results import admission_result
        saved = await asyncio.to_thread(admission_result, owner.db, receipt.admission_id)
        if saved is None:
            raise RuntimeStoreError('storage_unavailable')
        projected = result_task(rec, receipt, saved['result'])
        try:
            event = await write(tasks.publish, rec['task_id'], projected)
        except RuntimeStoreError as exc:
            if exc.reason != 'admission_conflict':
                raise
            current = await asyncio.to_thread(tasks.read, rec['task_id'])
            event = current['event']
            if not _same_terminal_projection(event, projected):
                raise
        rec['event'] = event
    task = rec['event']['task'] if rec['event'] else protocol.build_task(rec['task_id'], rec['context_id'],
        protocol.STATE_WORKING if receipt.status == 'started' else protocol.STATE_SUBMITTED)
    return {'task': task, 'event': rec['event'], 'admission_id': receipt.admission_id, 'acked': rec['acked']}


@asynccontextmanager
async def owner_client(home):
    """Attach to an existing owner only. Never call ensure/start or open its DB."""
    from hermes_cli.gateway_client import GATEWAY_WS_PROTOCOL, GatewayClient, GatewayClientError, _session_ticket, gateway_ws_target
    from hermes_cli.gateway_runtime import discover_gateway_endpoint
    from websockets.asyncio.client import connect
    home = await asyncio.to_thread(Path(home).resolve)
    ready = await asyncio.to_thread(discover_gateway_endpoint, home)
    if ready.state != 'ready' or ready.endpoint is None:
        raise GatewayClientError('gateway_not_ready')
    ticket = await asyncio.to_thread(_session_ticket, home, ready.endpoint)
    url, protocols = gateway_ws_target(ready.endpoint, ticket)
    async with connect(url, subprotocols=protocols, open_timeout=10, max_size=131072, proxy=None) as ws:
        if ws.subprotocol != GATEWAY_WS_PROTOCOL:
            raise GatewayClientError('gateway_protocol_mismatch')
        async with GatewayClient(ws) as client:
            yield client


async def observations(client, params):
    """One admission, then event-triggered reads; zero periodic receipt reads."""
    value = await client.rpc('a2a.event', event_task=params)
    yield value
    recovered_gap = False
    while value['event'] is None:
        event = await client.events.get()
        if isinstance(event, Exception):
            raise event
        frame = event.get('params') or {}
        gap = frame.get('type') == 'session.replay_gap'
        complete = (frame.get('type') == 'message.complete'
                    and (frame.get('payload') or {}).get('admission_id') == value['admission_id'])
        if gap and recovered_gap:
            raise RuntimeStoreError('unknown_execution')
        if gap or complete:
            recovered_gap |= gap
            value = await client.rpc('a2a.event', event_task={k: params[k] for k in ('agent', 'tenant', 'peer')} |
                                     {'op': 'observe', 'task_id': value['task']['id']})
            yield value
