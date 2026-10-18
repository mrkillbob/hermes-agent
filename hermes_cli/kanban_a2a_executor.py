"""Explicit A2A send/refresh with durable Kanban binding. No automatic registration."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sqlite3

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_coordinator_executor import (CoordinatorExecutor, CoordinatorLimits,
    bounded_connection, digest, event, validate_binding)
from hermes_cli.kanban_external import Artifact, Delivery, SubmissionRejected, TERMINAL, artifact_from_envelope
from plugins.platforms.a2a import protocol, security
from plugins.platforms.a2a.client import MAX_BYTES, decode_json, validate_url

_PREFIX = 'external_a2a_'
_SCHEMA = 'hermes.a2a.receipt.v1'


@dataclass(frozen=True)
class PeerPlan:
    executor_id: str
    url: str
    tenant: str = ''
    version: str = protocol.PROTOCOL_VERSION
    authority_id: str = 'fixture-only'
    return_mode: str = 'legacy'

    def __post_init__(self):
        validate_url(self.url)
        if (any(not isinstance(v, str) or len(v) > 2048 for v in asdict(self).values())
                or not self.executor_id.strip() or not self.authority_id.strip() or self.version != protocol.PROTOCOL_VERSION
                or self.return_mode not in {'legacy', 'events'}):
            raise ValueError('Explicit bounded peer plan required')


@dataclass(frozen=True)
class A2AStatus:
    state: str
    summary: str
    questions: tuple[str, ...] = ()
    task_id: str | None = None
    context_id: str | None = None


def _correlation(delivery):
    return {key: getattr(delivery, key) for key in ('task_id', 'run_id', 'key', 'payload_sha256')}



# Literal rooted references require a component; standalone punctuation is prose.
# Whitespace/quoting delimiters end a component; project-relative names are allowed.
_COMPONENT_START = r"[^\s/\\'\"<>(){}\[\],;]"
_LOCAL_PATH = re.compile(
    r'(?<![\w/\\])(?:[/\\]+|~[/\\]+|[A-Za-z]:[/\\]+|\.\.?[/\\]+)' + _COMPONENT_START
    + r'|(?<!\w)file:[/\\]*' + _COMPONENT_START, re.IGNORECASE)
_WEB_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _guard_assignment(delivery):
    for text in (delivery.title, delivery.body):
        if (security.redact_outbound(text) != text or _LOCAL_PATH.search(_WEB_URL.sub('', text))
                or any(value in text for value in (delivery.database, delivery.workspace, delivery.claim))):
            raise ValueError('Explicitly prepare assignment without local paths, authority or credentials')


def _message(delivery):
    # Local authority/path fields stay local, even though Delivery contains them.
    _guard_assignment(delivery)
    assignment = {'schema': 'hermes.a2a.assignment.v1', 'expected_result_schema': _SCHEMA, 'correlation': _correlation(delivery),
                  'assignment': {'title': delivery.title, 'body': delivery.body}}
    message = protocol.text_message(protocol.ROLE_USER, json.dumps(assignment, sort_keys=True))
    message['messageId'] = delivery.key
    return message



def _params(delivery, plan):
    return {'message': _message(delivery), **({'tenant': plan.tenant} if plan.tenant else {})}


def _plan_dict(plan):
    value = asdict(plan)
    if plan.return_mode == 'legacy':
        value.pop('return_mode')  # Preserve pre-event journal bindings byte-for-byte.
    return value


def _send_intent(delivery, plan):
    return {'plan': _plan_dict(plan), 'message_id': delivery.key, 'request_sha256': digest(_params(delivery, plan))}


def _task(response, method):
    result = response['result']
    if method == 'SendMessage':
        if not isinstance(result, dict) or set(result) != {'task'}:
            raise ValueError('Stateful SendMessage response required')
        result = result['task']
    if not isinstance(result, dict):
        raise ValueError('Stateful A2A Task required')
    task = result
    state = (task.get('status') or {}).get('state')
    if (any(not isinstance(task.get(k), str) or not task[k].strip() or len(task[k]) > 1024 for k in ('id', 'contextId'))
            or not isinstance(state, str) or not state or len(state) > 128):
        raise ValueError('Bounded task identity/state required')
    return task


def _receipt(delivery, message, kind):
    parts = message.get('parts')
    if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict) or not isinstance(parts[0].get('text'), str):
        raise ValueError('One structured receipt text part required')
    value = decode_json(parts[0]['text'])
    required = {'schema', 'correlation', 'kind', 'summary', 'findings', 'questions'}
    if (not isinstance(value, dict) or set(value) != required or value['schema'] != _SCHEMA or value['kind'] != kind
            or not isinstance(value['correlation'], dict) or type(value['correlation'].get('run_id')) is not int
            or value['correlation'] != _correlation(delivery)
            or any(not isinstance(value[k], list) for k in ('findings', 'questions'))):
        raise ValueError('Unbound application receipt')
    return Artifact(delivery, kind, value['summary'], tuple(value['findings']), tuple(value['questions']))


def _artifact(delivery, task):
    state = task['status']['state']
    if state == protocol.STATE_SUBMITTED:
        return Artifact(delivery, 'ack', 'A2A peer acknowledged the bound task')
    if state == protocol.STATE_WORKING:
        return Artifact(delivery, 'started', 'A2A peer reports the bound task working')
    if state in {protocol.STATE_COMPLETED, protocol.STATE_FAILED}:
        artifacts = task.get('artifacts')
        if not isinstance(artifacts, list) or len(artifacts) != 1 or not isinstance(artifacts[0], dict):
            raise ValueError('One bound result artifact required')
        if not isinstance(artifacts[0].get('artifactId'), str) or not artifacts[0]['artifactId'].strip():
            raise ValueError('Result artifact identity required')
        return _receipt(delivery, artifacts[0], 'completed' if state == protocol.STATE_COMPLETED else 'error')
    return None


class A2AExecutor:
    """The caller supplies a trusted client/plan; task text cannot select transport."""
    def __init__(self, database: Path, plan: PeerPlan, client, limits=None):
        if not isinstance(plan, PeerPlan):
            raise ValueError('Typed owner peer plan required')
        self.plan, self.client = plan, client
        self.journal = CoordinatorExecutor(database, plan.executor_id, limits or CoordinatorLimits())
        self._check_client()

    def _check_client(self):
        if self.client.url != self.plan.url or self.client.version != self.plan.version:
            raise ValueError('Client differs from owner peer plan')

    def _intent(self, delivery):
        self._check_client()
        old = self.journal.read(delivery, _PREFIX + 'send_intent')
        if old is not None and old != {'binding': digest(asdict(delivery)), **_send_intent(delivery, self.plan)}:
            raise ValueError('Peer plan/request changed after send intent')
        return old

    def _latest(self, delivery):
        with bounded_connection(self.journal.database, self.journal.limits.lookup_seconds) as conn:
            validate_binding(conn, delivery, self.journal.database, self.plan.executor_id)
            value = event(conn, delivery, _PREFIX + 'observation')
            if value is not None and (value.get('binding') != digest(asdict(delivery)) or value.get('plan') != _plan_dict(self.plan)):
                raise ValueError('Foreign A2A observation')
            return value

    def _save(self, delivery, response=None, error_class=None, method=None):
        value = {'binding': digest(asdict(delivery)), 'plan': _plan_dict(self.plan),
                 'response': response, 'error_class': error_class, 'method': method}
        if len(json.dumps(value, allow_nan=False).encode()) > 65536:
            raise ValueError('Observation exceeds journal bound')
        with bounded_connection(self.journal.database, self.journal.limits.submit_seconds, writable=True) as conn:
            validate_binding(conn, delivery, self.journal.database, self.plan.executor_id, owned=True)
            old = event(conn, delivery, _PREFIX + 'observation')
            if old != value:
                kb._append_event(conn, delivery.task_id, _PREFIX + 'observation', value, run_id=delivery.run_id)

    def _failure(self, delivery, error):
        # An input-required frame is already a useful, validated owner
        # observation. Preserve it if a later delivery-ACK RPC fails: replacing
        # it with an error-only observation would make explicit recovery able
        # to ACK the peer while losing the question needed by Kanban attention.
        latest = self._latest(delivery)
        if latest is not None and latest['response'] is not None:
            if self._cached_terminal(delivery) is not None:
                return
            status = self.status(delivery)
            if status is not None and status.state == protocol.STATE_INPUT_REQUIRED and status.questions:
                return
        self._save(delivery, error_class=type(error).__name__)

    def _remote(self, delivery):
        remote = self.journal.read(delivery, _PREFIX + 'remote')
        if remote is not None and (set(remote) != {'binding', 'task_id', 'context_id', 'plan'}
                or remote['plan'] != _plan_dict(self.plan)
                or any(not isinstance(remote[k], str) or not remote[k].strip() or len(remote[k]) > 1024
                       for k in ('task_id', 'context_id'))):
            raise ValueError('Malformed remote task binding')
        return remote

    def _ingest(self, delivery, response, method):
        task = _task(response, method)
        remote = {'task_id': task['id'], 'context_id': task['contextId'], 'plan': _plan_dict(self.plan)}
        old = self._remote(delivery)
        if old is not None and {k: old[k] for k in remote} != remote:
            raise ValueError('A2A task/context changed')
        saved = self._cached_terminal(delivery)
        if saved is not None:
            candidate = _artifact(delivery, task)
            if candidate is not None and candidate.kind in TERMINAL and candidate != saved:
                raise ValueError('Conflicting peer terminal evidence')
            return saved
        self.journal.record(delivery, _PREFIX + 'remote', remote)
        self._save(delivery, response=response, method=method)
        try:
            artifact = _artifact(delivery, task)
        except (ValueError, TypeError, KeyError, AttributeError):
            return None
        if artifact is not None and artifact.kind in TERMINAL:
            self.journal.record(delivery, _PREFIX + 'terminal',
                                {'envelope': {'artifact': asdict(artifact), 'sha256': artifact.sha256}})
        return artifact

    def _cached_terminal(self, delivery):
        saved = self.journal.read(delivery, _PREFIX + 'terminal')
        if saved is not None:
            result = artifact_from_envelope(saved['envelope'])
            if result.delivery != delivery or result.kind not in {'completed', 'error'}:
                raise ValueError('Foreign saved terminal result')
            return result
        return None

    def submit(self, delivery: Delivery):
        old = self._intent(delivery)
        accepted = Artifact(delivery, 'accepted', 'A2A send intent retained; peer acceptance unproven')
        if old is not None:
            return self.lookup(delivery) or accepted
        try:
            params = _params(delivery, self.plan)
            if len(json.dumps(params).encode()) > MAX_BYTES - 1024:
                raise ValueError('Assignment exceeds bounded A2A request')
        except ValueError:
            # This scope is pure preparation, before the durable send intent and RPC.
            # Never classify an existing send or a transport failure as definitely unsent.
            raise SubmissionRejected('Assignment rejected before submission') from None
        intent = _send_intent(delivery, self.plan)
        if not self.journal.record(delivery, _PREFIX + 'send_intent', intent, exclusive=True):
            self._intent(delivery)
            return self.lookup(delivery) or accepted
        try:
            if self.plan.return_mode == 'events':
                from hermes_cli.kanban_a2a_events import consume
                return consume(self, delivery, 'SendStreamingMessage', params) or accepted
            response = self.client.rpc('SendMessage', params, delivery.key + ':send', timeout=self.journal.limits.submit_seconds)
            return self._ingest(delivery, response, 'SendMessage') or accepted
        except (ValueError, TypeError, KeyError, AttributeError, OSError, sqlite3.Error) as error:
            # No exception text (which may contain remote output or credentials) is retained.
            self._failure(delivery, error)
            return (self.lookup(delivery) or accepted) if self.plan.return_mode == 'events' else accepted

    def lookup(self, delivery: Delivery):
        if self._intent(delivery) is None:
            return None
        terminal = self._cached_terminal(delivery)
        if terminal is not None:
            return terminal
        value = self._latest(delivery)
        if value is None:
            return Artifact(delivery, 'accepted', 'A2A send intent retained; peer acceptance unproven')
        if value['response'] is None:
            return None
        try:
            task = _task(value['response'], value['method'])
            remote = self._remote(delivery)
            if remote is None or task['id'] != remote['task_id'] or task['contextId'] != remote['context_id']:
                raise ValueError('Unbound cached observation')
            return _artifact(delivery, task)
        except (ValueError, TypeError, KeyError, AttributeError):
            return None

    def recover_events_once(self, delivery: Delivery, *, recovery_id: str):
        if self.plan.return_mode != 'events':
            raise ValueError('Event return mode required')
        from hermes_cli.kanban_a2a_events import recover_once
        return recover_once(self, delivery, recovery_id)

    def refresh_once(self, delivery: Delivery):
        if self.plan.return_mode == 'events':
            raise ValueError('Periodic refresh is disabled in event return mode')
        if self._intent(delivery) is None:
            raise ValueError('Send intent required before refresh')
        saved = self.lookup(delivery)
        if saved is not None and saved.kind in TERMINAL:
            return saved
        remote = self._remote(delivery)
        if remote is None:
            return None
        with bounded_connection(self.journal.database, self.journal.limits.lookup_seconds) as conn:
            validate_binding(conn, delivery, self.journal.database, self.plan.executor_id, owned=True)
        params = {'id': remote['task_id'], **({'tenant': self.plan.tenant} if self.plan.tenant else {})}
        try:
            response = self.client.rpc('GetTask', params, delivery.key + ':get', timeout=self.journal.limits.lookup_seconds)
            return self._ingest(delivery, response, 'GetTask')
        except (ValueError, TypeError, KeyError, AttributeError, OSError, sqlite3.Error) as error:
            self._failure(delivery, error)
            return None

    def status(self, delivery: Delivery):
        if self._intent(delivery) is None:
            return None
        saved = self._cached_terminal(delivery)
        if saved is not None:
            remote = self._remote(delivery)
            return A2AStatus(protocol.STATE_COMPLETED if saved.kind == 'completed' else protocol.STATE_FAILED,
                             saved.summary, saved.questions, remote['task_id'], remote['context_id'])
        value = self._latest(delivery)
        if value is None or value['response'] is None:
            return A2AStatus('unknown', 'Peer outcome unverified')
        task = _task(value['response'], value['method'])
        questions = ()
        if task['status']['state'] == protocol.STATE_INPUT_REQUIRED:
            try:
                artifact = _receipt(delivery, task['status']['message'], 'question')
                questions = artifact.questions
            except (ValueError, TypeError, KeyError, AttributeError):
                pass
        return A2AStatus(task['status']['state'], 'Saved peer observation; lifecycle authority remains in Kanban',
                         questions, task['id'], task['contextId'])
