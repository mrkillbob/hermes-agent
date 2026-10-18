"""Opt-in existing A2A endpoint -> canonical owner -> durable SSE delivery."""
import asyncio
from copy import deepcopy

from . import protocol
from .client import MAX_BYTES

ACK_SCHEMA = 'hermes.a2a.delivery_ack.v1'
LOOKUP_SCHEMA = 'hermes.a2a.lookup.v1'
EVENT_MODE = 'hermes.events.v1'
DELIVERY = 'hermes.delivery.v1'


def ack_data(params):
    parts = (params.get('message') or {}).get('parts')
    if not isinstance(parts, list) or len(parts) != 1 or set(parts[0]) != {'data'}:
        raise ValueError('Typed delivery ACK required')
    data = parts[0]['data']
    if (not isinstance(data, dict) or set(data) != {'schema', 'task_id', 'event_id', 'sha256'}
            or data['schema'] != ACK_SCHEMA or any(not isinstance(v, str) for v in data.values())):
        raise ValueError('Typed delivery ACK required')
    return data


def wire_task(value):
    task = deepcopy(value['task'])
    task['metadata'] = {EVENT_MODE: {'ack_schema': ACK_SCHEMA}}
    if value['event']:
        task['metadata'][DELIVERY] = {k: value['event'][k] for k in ('id', 'sha256')}
        task['metadata'][DELIVERY]['acknowledged'] = bool(value.get('acked', False))
    return task


async def serve(adapter, handler, method, req_id, params, peer, agent, *, client_factory=None):
    from gateway.session_a2a_events import observations, owner_client
    from plugins.platforms.a2a.adapter import _profile_home
    factory = client_factory or owner_client
    base = dict(agent=str(agent.get('slug') or 'local'), tenant=str(agent.get('tenant') or ''), peer=peer)
    profile = agent.get('profile') or (adapter._active_profile if agent.get('local', True) else agent.get('slug'))
    home = adapter._event_owner_home if agent.get('local', True) else _profile_home(profile)
    if not home:
        raise ValueError('Explicit owner profile required')
    async with asyncio.timeout(120):
        async with factory(home) as client:
            if method == '_rpc_message_send':
                ack = ack_data(params)
                result = await client.rpc('a2a.event', event_task=base | {'op': 'ack',
                    'task_id': ack['task_id'], 'event_id': ack['event_id'], 'sha256': ack['sha256']})
                message = {'role': protocol.ROLE_AGENT, 'messageId': ack['event_id'], 'parts': [{'data': result}]}
                return handler._json(200, protocol.jsonrpc_result(req_id, {'message': message}))
            if method not in {'_rpc_message_stream', '_rpc_tasks_subscribe', '_rpc_tasks_get'}:
                raise ValueError('Reliable mode requires stream, subscribe, get or delivery ACK')
            if method == '_rpc_message_stream':
                message_id = protocol.extract_message_id(params)
                text = protocol.extract_text(params)
                request = base | {'op': 'accept', 'message_id': message_id, 'text': text,
                                  'context_id': protocol.extract_context_id(params)}
            else:
                request = base | {'op': 'observe', 'task_id': params.get('id') or params.get('taskId') or ''}
            if method == '_rpc_tasks_get':
                lookup = (params.get('metadata') or {}).get(LOOKUP_SCHEMA)
                if lookup is not None:
                    if not isinstance(lookup, dict) or set(lookup) != {'message_id', 'request_sha256'}:
                        raise ValueError('Bound read-only lookup required')
                    request = base | {'op': 'find', **lookup}
                value = await client.rpc('a2a.event', event_task=request)
                return handler._json(200, protocol.jsonrpc_result(req_id, wire_task(value)))
            opened = False
            async for value in observations(client, request):
                payload = protocol.sse_data(protocol.stream_task(wire_task(value)), req_id)
                if len(payload.encode()) > MAX_BYTES:
                    raise ValueError('A2A event exceeds frame bound')
                if not opened:
                    handler._a2a_event_open = True
                    adapter._sse_headers(handler)
                    opened = True
                adapter._sse_write(handler, payload)
            adapter._sse_write(handler, protocol.sse_done())


def dispatch(adapter, handler, method, req_id, params, peer, agent):
    # Runs on the existing HTTP request thread; disconnect only detaches delivery.
    # Canonical execution has its own durable owner and is not cancelled here.
    try:
        return asyncio.run(serve(adapter, handler, method, req_id, params, peer, agent))
    except (BrokenPipeError, ConnectionResetError):
        return None
    except Exception:  # health: allow BLE001 -- owner/transport errors are untrusted; emit fixed refusal before headers, otherwise close without inventing an outcome
        # No untrusted owner/transport exception text in a peer response.
        if getattr(handler, '_a2a_event_open', False):
            handler.close_connection = True
            return None
        return handler._json(200, protocol.jsonrpc_error(req_id, protocol.ERR_INVALID_PARAMS, 'Event return unavailable; retain task identity'))
