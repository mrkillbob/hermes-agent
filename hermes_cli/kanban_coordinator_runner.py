"""Explicit one-shot POSIX fixture receiver. No live executable or account path."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from typing import Literal
from uuid import uuid4

import psutil

from hermes_cli.kanban_coordinator_executor import CoordinatorExecutor, CoordinatorLimits, bounded_connection, digest
from hermes_cli.kanban_external import Artifact, ExternalLanes, Lane, TERMINAL

FIXTURE_SHA256 = '5cc6a054c2eeb89895ff2f4249260d6408030f2e9cae8e4d864cf5311f73180e'
FIXTURE_PROGRAM = Path(__file__).resolve().parents[1] / 'tests/hermes_cli/fixtures/fake_coordinator_cli.py'
BEHAVIORS = frozenset({'success', 'hold', 'question', 'fail', 'malformed', 'wrong_binding',
                       'provider', 'fallback', 'ignore_term', 'overflow', 'descendant'})


def input_bytes(delivery) -> bytes:
    return json.dumps({'fixture': True, 'task_id': delivery.task_id, 'run_id': delivery.run_id,
                       'key': delivery.key, 'payload_sha256': delivery.payload_sha256,
                       'assignment': 'Synthetic arithmetic: 2 + 2'}, sort_keys=True).encode()


@dataclass(frozen=True)
class ExecutionPlan:
    mode: str
    program_sha256: str
    evidence_root: Path
    behavior: str
    input_sha256: str
    coordinator_id: str = 'fixture-coordinator'

    def identity(self):
        return {'mode': self.mode, 'program_sha256': self.program_sha256, 'evidence_root': str(self.evidence_root),
                'behavior': self.behavior, 'input_sha256': self.input_sha256, 'coordinator_id': self.coordinator_id,
                'interpreter': str(Path(sys.executable).resolve())}

    def validate(self, delivery):
        if os.name != 'posix' or self.mode != 'fixture' or self.behavior not in BEHAVIORS:
            raise ValueError('Only the pinned POSIX fixture is executable')
        if not delivery.title.startswith('fixture:') or delivery.body != 'inert fixture input':
            raise ValueError('Only explicitly synthetic task input is executable')
        if (self.program_sha256 != FIXTURE_SHA256 or hashlib.sha256(FIXTURE_PROGRAM.read_bytes()).hexdigest() != FIXTURE_SHA256
                or self.input_sha256 != hashlib.sha256(input_bytes(delivery)).hexdigest()):
            raise ValueError('Execution source or input integrity mismatch')
        root = self.evidence_root
        if (not isinstance(root, Path) or not root.is_absolute() or root != root.resolve()
                or root.parent != Path(delivery.workspace).parent or root.name != 'evidence-' + delivery.task_id):
            raise ValueError('Evidence must use the exact private task directory')


@dataclass(frozen=True)
class ReceiverOutcome:
    state: Literal['completed', 'blocked', 'error_settled', 'uncertain']
    key: str
    receipt_sha256: str | None = None
    evidence_mode: Literal['inert_fixture'] = field(default='inert_fixture', init=False)


def _apply(executor, delivery, artifact):
    lanes = ExternalLanes({delivery.lane: Lane(executor.coordinator_id, executor)})
    deadline = time.monotonic() + executor.limits.submit_seconds
    with closing(sqlite3.connect(executor.database.as_uri() + '?mode=rw', uri=True, timeout=.2)) as conn:
        conn.row_factory = sqlite3.Row
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
        applied = lanes.receive(conn, artifact)
    if artifact.kind not in TERMINAL or not applied:
        return ReceiverOutcome('uncertain', delivery.key)
    state = {'completed': 'completed', 'error': 'error_settled'}.get(artifact.kind, 'blocked')
    return ReceiverOutcome(state, delivery.key, artifact.sha256)


def _uncertain(executor, delivery, reason):
    executor.record(delivery, 'external_receiver_uncertain', {'reason': reason}, exclusive=True)
    return ReceiverOutcome('uncertain', delivery.key)


def _private_file(path, data=None):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    stream = os.fdopen(descriptor, 'wb')
    if data is not None:
        with stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return None
    return stream


def _group_exists(pid):
    try:
        os.killpg(pid, 0)  # windows-footgun: ok -- receive_once validates POSIX before process operations
        return True
    except ProcessLookupError:
        return False


def _birth(pid):
    try:
        return psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        return None


def _signal_owned(process, birth, sig):
    # Never signal a group after losing the live leader's identity.
    if process.poll() is not None or birth is None or _birth(process.pid) != birth:
        return False
    try:
        os.killpg(process.pid, sig)  # windows-footgun: ok -- only the validated POSIX fixture receiver reaches this
        return True
    except ProcessLookupError:
        return False


def _abort(process, birth, limits):
    for sig, grace in ((signal.SIGTERM, limits.terminate_grace_seconds), (getattr(signal, 'SIGKILL', signal.SIGTERM), limits.kill_grace_seconds)):
        if not _signal_owned(process, birth, sig):
            break
        try:
            process.wait(timeout=grace)
            break
        except subprocess.TimeoutExpired:
            continue
    return process.poll() is not None and not _group_exists(process.pid)


def _drain(process, outputs, deadline, cap):
    counts = {'stdout': 0, 'stderr': 0}
    reason = None
    with selectors.DefaultSelector() as selector:
        for name in counts:
            pipe = getattr(process, name)
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = 'execution_deadline'
                break
            for key, _mask in selector.select(min(remaining, .1)):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                name = key.data
                allowed = min(len(data), cap - counts[name])
                outputs[name].write(data[:allowed])
                counts[name] += allowed
                if allowed < len(data):
                    reason = 'output_limit'
                    break
            if reason:
                break
    if reason is None:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            reason = 'execution_deadline'
    return reason, counts


def _execute(executor, delivery, plan, seconds):
    root, limits = plan.evidence_root, executor.limits
    data = input_bytes(delivery)
    root.mkdir(mode=0o700)
    _private_file(root / 'input.json', data)
    outputs = {}
    launch = uuid4().hex
    executor.record(delivery, 'external_launch_intent', {'launch_id': launch, 'plan_sha256': digest(plan.identity())})
    with closing(_private_file(root / 'stdout')) as out, closing(_private_file(root / 'stderr')) as err, (root / 'input.json').open('rb') as prompt:
        outputs.update(stdout=out, stderr=err)
        started = time.monotonic()
        process = subprocess.Popen([sys.executable, '-I', str(FIXTURE_PROGRAM), plan.behavior],
            stdin=prompt, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=root,
            env={'HOME': str(root), 'PATH': str(Path(sys.executable).parent), 'LANG': 'C.UTF-8'}, start_new_session=True)
        birth = _birth(process.pid)
        reason, counts, settled = 'receiver_interrupted', {'stdout': 0, 'stderr': 0}, False
        try:
            executor.record(delivery, 'external_process_started', {'launch_id': launch, 'pid': process.pid, 'birth': birth})
            _apply(executor, delivery, Artifact(delivery, 'started', 'Inert process started; model acceptance unproven'))
            reason, counts = _drain(process, outputs, started + seconds, limits.stream_bytes)
        finally:
            if process.poll() is None:
                settled = _abort(process, birth, limits)
            else:
                settled = not _group_exists(process.pid)
            process.stdout.close()
            process.stderr.close()
            for stream in outputs.values():
                stream.flush()
                os.fsync(stream.fileno())
        record = {'launch_id': launch, 'pid': process.pid, 'birth': birth, 'exit_code': process.returncode,
                  'settled': settled, 'reason': reason, 'input_sha256': plan.input_sha256,
                  **{name + '_bytes': count for name, count in counts.items()}}
    for name in outputs:
        record[name + '_sha256'] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    executor.record(delivery, 'external_process_exit', record)
    return record


def _validate_result(delivery, raw):
    value = json.loads(raw)
    models, usage = value.get('modelUsage'), value.get('usage')
    if (value.get('is_error') is not False or not isinstance(models, dict) or not models
            or not isinstance(usage, dict) or 'fallback_credit' not in usage or usage['fallback_credit'] is not None):
        raise ValueError('Unqualified fixture outer result')
    if any(not name.startswith('claude-sonnet-') or model.get('provider') != 'firstParty' for name, model in models.items()):
        raise ValueError('Unqualified fixture provider')
    result = json.loads(value['result'])
    required = {'task_id', 'run_id', 'key', 'payload_sha256', 'acknowledgment', 'kind', 'summary', 'findings', 'questions'}
    if (not isinstance(result, dict) or set(result) != required or type(result['run_id']) is not int
            or any(not isinstance(result[name], list) for name in ('findings', 'questions'))):
        raise ValueError('Malformed fixture result schema')
    if (any(result.get(key) != getattr(delivery, key) for key in ('task_id', 'run_id', 'key', 'payload_sha256'))
            or result.get('acknowledgment') != 'accepted' or result.get('kind') not in TERMINAL):
        raise ValueError('Unbound fixture result')
    return Artifact(delivery, result['kind'], result['summary'], tuple(result['findings']), tuple(result['questions']))


def _read_evidence(path, cap):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with closing(os.fdopen(descriptor, 'rb')) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > cap:
            raise ValueError('Unbounded evidence file')
        data = stream.read(cap + 1)
        if len(data) > cap:
            raise ValueError('Evidence grew beyond cap')
        return data


def _evidence(executor, delivery, plan, record):
    launch = executor.read(delivery, 'external_launch_intent')
    started = executor.read(delivery, 'external_process_started')
    if (launch is None or started is None or record['launch_id'] != launch['launch_id']
            or any(record[key] != started[key] for key in ('launch_id', 'pid', 'birth'))
            or record['input_sha256'] != plan.input_sha256):
        raise ValueError('Unbound process exit')
    data = {name: _read_evidence(plan.evidence_root / name, executor.limits.stream_bytes)
            for name in ('stdout', 'stderr')}
    for name, raw in data.items():
        if len(raw) != record[name + '_bytes'] or hashlib.sha256(raw).hexdigest() != record[name + '_sha256']:
            raise ValueError('Output integrity mismatch')
    if hashlib.sha256(_read_evidence(plan.evidence_root / 'input.json', 65536)).hexdigest() != plan.input_sha256:
        raise ValueError('Input integrity mismatch')
    return data


def _finish(executor, delivery, plan, record):
    if record['settled'] is not True:
        return _uncertain(executor, delivery, 'process_group_unsettled')
    try:
        data = _evidence(executor, delivery, plan, record)
    except (ValueError, OSError, KeyError):
        return _uncertain(executor, delivery, 'evidence_integrity')
    if record['reason'] is not None or record['exit_code'] != 0:
        return _apply(executor, delivery, Artifact(delivery, 'error', 'Inert child failed or exceeded execution limits'))
    try:
        artifact = _validate_result(delivery, data['stdout'])
    except (ValueError, TypeError, KeyError, AttributeError):
        return _uncertain(executor, delivery, 'result_validation')
    return _apply(executor, delivery, artifact)


def receive_once(database: Path, task_id: str, run_id: int, key: str,
                 plan: ExecutionPlan, limits: CoordinatorLimits) -> ReceiverOutcome:
    executor = CoordinatorExecutor(database, plan.coordinator_id, limits)
    delivery = executor.delivery(task_id, run_id, key)
    plan.validate(delivery)
    old = executor.read(delivery, 'external_receiver_claim')
    if old is not None:
        if old['plan_sha256'] != digest(plan.identity()):
            raise ValueError('Receiver plan changed')
        receipt = executor.lookup(delivery)
        if receipt is not None and receipt.kind in TERMINAL:
            return _apply(executor, delivery, receipt)
        recorded_exit = executor.read(delivery, 'external_process_exit')
        if recorded_exit is not None:
            return _finish(executor, delivery, plan, recorded_exit)
        return ReceiverOutcome('uncertain', key)
    if executor.lookup(delivery) is None:
        raise ValueError('Coordinator offer required')
    if not executor.record(delivery, 'external_receiver_claim',
                           {'receiver_instance': uuid4().hex, 'plan_sha256': digest(plan.identity())}, exclusive=True):
        return ReceiverOutcome('uncertain', key)
    _apply(executor, delivery, Artifact(delivery, 'ack', 'Coordinator durable inbox accepted; no model ACK'))
    with bounded_connection(executor.database, limits.lookup_seconds) as conn:
        row = conn.execute('SELECT started_at,max_runtime_seconds FROM task_runs WHERE id=?', (run_id,)).fetchone()
    seconds = limits.execution_seconds
    if row and row[1]:
        seconds = min(seconds, row[0] + row[1] - time.time())
    if seconds <= 0:
        return _apply(executor, delivery, Artifact(delivery, 'error', 'Task deadline elapsed before inert launch'))
    record = _execute(executor, delivery, plan, seconds)
    return _finish(executor, delivery, plan, record)
