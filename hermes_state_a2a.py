"""Owner-only A2A task/delivery projection; canonical admissions own execution."""
from __future__ import annotations

import hashlib
import json

from hermes_state_runtime import RuntimeStoreError, _epoch

_PREFIX = 'a2a.event.v1:'
_MAX_TASKS = 500
_MAX_BYTES = 65536


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class EventTasks:
    """No execution queue, automatic pruning, timer or external connection.

    Constructed only by the canonical owner with its epoch and trusted local
    producer + authenticated remote peer scope. Hashes are consistency, not auth.
    """
    def __init__(self, db, epoch, scope):
        if (not isinstance(scope, tuple) or len(scope) != 4
                or any(not isinstance(s, str) or len(s) > 1024 for s in scope)
                or not scope[0] or not scope[1] or not scope[3]):
            raise RuntimeStoreError('invalid_params')
        self.db, self.epoch, self.scope = db, epoch, scope
        self.prefix = _PREFIX + digest(scope) + ':'

    def _key(self, task_id):
        if not isinstance(task_id, str) or not task_id.startswith('task-') or len(task_id) != 69:
            raise RuntimeStoreError('invalid_params')
        return self.prefix + task_id

    def _read(self, conn, task_id):
        _epoch(conn, self.epoch)
        row = conn.execute('SELECT value FROM state_meta WHERE key=?', (self._key(task_id),)).fetchone()
        if row is None:
            raise RuntimeStoreError('not_found')
        value = json.loads(row[0])
        if value['scope'] != list(self.scope) or value['task_id'] != task_id:
            raise RuntimeStoreError('admission_conflict')
        return value

    def _save(self, conn, value):
        raw = encoded(value)
        if len(raw.encode()) > _MAX_BYTES:
            raise RuntimeStoreError('invalid_params')
        conn.execute('INSERT OR REPLACE INTO state_meta(key,value) VALUES (?,?)',
                     (self._key(value['task_id']), raw))
        return value

    def reserve(self, message_id, request, correlation=None):
        if (not isinstance(message_id, str) or not 0 < len(message_id) <= 1024
                or not isinstance(request, dict) or set(request) != {'context_id', 'text'}
                or any(not isinstance(v, str) for v in request.values())
                or len(request['context_id']) > 1024 or not request['text']
                or len(encoded(request).encode()) > 49152):
            raise RuntimeStoreError('invalid_params')
        task_id = 'task-' + digest([self.scope, message_id])
        fingerprint = digest([request, correlation])
        def write(conn):
            _epoch(conn, self.epoch)
            old = conn.execute('SELECT value FROM state_meta WHERE key=?', (self._key(task_id),)).fetchone()
            if old:
                saved = self._read(conn, task_id)
                if saved['request_sha256'] != fingerprint:
                    raise RuntimeStoreError('admission_conflict')
                return saved
            count = conn.execute('SELECT COUNT(*) FROM state_meta WHERE substr(key,1,?)=?',
                                 (len(self.prefix), self.prefix)).fetchone()[0]
            if count >= _MAX_TASKS:
                raise RuntimeStoreError('capacity_exceeded')
            return self._save(conn, {'task_id': task_id, 'context_id': request['context_id'] or 'ctx-' + digest(task_id),
                'scope': list(self.scope), 'request_sha256': fingerprint, 'correlation': correlation, 'admission': None, 'event': None, 'acked': False})
        return self.db._execute_write(write)

    def find(self, message_id, request_sha256):
        value = self.read('task-' + digest([self.scope, message_id]))
        if value['request_sha256'] != request_sha256:
            raise RuntimeStoreError('admission_conflict')
        return value

    def read(self, task_id):
        with self.db._read_ctx() as conn:
            return self._read(conn, task_id)

    def _update(self, task_id, change):
        def write(conn):
            value = self._read(conn, task_id)
            change(value)
            return self._save(conn, value)
        return self.db._execute_write(write)

    def bind(self, task_id, admission):
        if (not isinstance(admission, dict) or set(admission) != {'session_id', 'admission_id'}
                or any(not isinstance(v, str) or not 0 < len(v) <= 1024 for v in admission.values())):
            raise RuntimeStoreError('invalid_params')
        def change(value):
            if value['admission'] not in (None, admission):
                raise RuntimeStoreError('admission_conflict')
            value['admission'] = admission
        return self._update(task_id, change)

    def publish(self, task_id, task):
        event = {'id': 'event-' + digest(task), 'sha256': digest(task), 'task': task}
        def change(value):
            if (task.get('id') != task_id or task.get('contextId') != value['context_id']
                    or value['admission'] is None or value['event'] not in (None, event)):
                raise RuntimeStoreError('admission_conflict')
            value['event'] = event
        return self._update(task_id, change)['event']

    def ack(self, task_id, event_id, sha256):
        def change(value):
            event = value['event']
            if event is None or event['id'] != event_id or event['sha256'] != sha256:
                raise RuntimeStoreError('admission_conflict')
            value['acked'] = True
        return self._update(task_id, change)
