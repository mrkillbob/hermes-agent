"""Commit a prepared summary, physical target, generation and receipt atomically."""
import uuid
from hermes_state_mutation_prepared import validate_prepared


def _bind_carried_uids(db, conn, target, prepared):
    """Link exact legacy originals to their copies before either changes display generation."""
    from agent.message_metadata import ABSORBED_MESSAGE_UIDS, MESSAGE_UID
    carried = prepared['carried_messages']
    resolved = set(db._resolve_carried_row_ids(conn, target, carried))
    remap = {}
    for message in carried:
        row_id = message.get('_row_id')
        if row_id not in resolved:
            continue
        row = conn.execute('SELECT message_uid FROM messages WHERE session_id=? AND id=?',
                           (target, row_id)).fetchone()
        uid = row[0] or message[MESSAGE_UID]
        conn.execute('UPDATE messages SET message_uid=? WHERE session_id=? AND id=? AND message_uid IS NULL',
                     (uid, target, row_id))
        # A migration may have filled the UID since preparation; the durable value wins.
        remap[message[MESSAGE_UID]] = uid
    for message in prepared['messages']:
        if message.get(MESSAGE_UID) in remap:
            message[MESSAGE_UID] = remap[message[MESSAGE_UID]]
        if ABSORBED_MESSAGE_UIDS in message:
            message[ABSORBED_MESSAGE_UIDS] = [remap.get(uid, uid) for uid in message[ABSORBED_MESSAGE_UIDS]]


def compress_in_transaction(db, conn, session_id, payload, prepared):
    snapshot = validate_prepared(db, conn, session_id, prepared)
    policy = snapshot['receipt']['policy']
    target = snapshot['target']
    from hermes_state_worker_compression import archive_on_connection, publish_on_connection
    # No worker/turn/compactor can be active: validate_prepared checks those
    # obligations on this exact connection. No separate legacy transaction runs.
    if prepared['in_place']:
        child = target
        _bind_carried_uids(db, conn, target, prepared)
        archive_on_connection(db, conn, target, prepared['messages'], carried_messages=prepared['carried_messages'])
        affected = {session_id, target}
    else:
        child = uuid.uuid4().hex
        publish_on_connection(db, conn, parent_session_id=target, child_session_id=child,
            source=policy['source'], model=policy['model'], messages=prepared['messages'],
            model_config={'_compressed_from': target}, cwd=policy['cwd'], require_compression_lease=False)
        affected = set()  # Rotation publication already advances the logical revision.
    conn.execute('UPDATE sessions SET runtime_generation=runtime_generation+1 WHERE id=?', (session_id,))
    generation = conn.execute('SELECT runtime_generation FROM sessions WHERE id=?', (session_id,)).fetchone()[0]
    return affected, {'target_session_id': child, 'previous_target_session_id': target,
                   'execution_generation': generation, 'message_count': len(prepared['messages']),
                   'summary': prepared.get('summary')}
