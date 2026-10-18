"""Opt-in A2A registry for normal dispatch, bound to the current profile's config."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_external import Artifact, Delivery, ExternalLanes, Lane, _owns
from hermes_cli.kanban_coordinator_executor import envelope, event

_MAX_LANES = 4
_REFRESH_SECONDS = 60
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')


def _identity(value):
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError('Explicit external executor/authority identity required')
    return value


def _peer(config, entry):
    if not isinstance(entry, dict) or set(entry) != {'peer', 'executor_id', 'authority_id'}:
        raise ValueError('External lane requires peer, executor_id and authority_id')
    name = entry['peer']
    peers = config.get('a2a_agents')
    if not isinstance(name, str) or _ID.fullmatch(name) is None or not isinstance(peers, dict) or name not in peers:
        raise ValueError('External lane requires an existing named A2A peer')
    peer = peers[name]
    if not isinstance(peer, dict):
        raise ValueError('Malformed configured A2A peer')
    url = peer.get('url')
    if not isinstance(url, str):
        raise ValueError('External peer requires an explicit RPC URL')
    parsed = urlsplit(url)
    if parsed.port == 0 or parsed.query or (parsed.scheme != 'https' and not (
            parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'})):
        raise ValueError('External peers require HTTPS or loopback HTTP, without query credentials')
    return peer


def _headers(peer):
    from plugins.platforms.a2a.tools import _auth_header

    auth = peer.get('auth', {})
    if auth is None:
        auth = {}
    if not isinstance(auth, dict) or set(auth) - {'type', 'token'}:
        raise ValueError('Only the existing A2A bearer authentication is supported')
    kind = auth.get('type', 'none')
    if kind == 'none' and not auth.get('token'):
        return {}
    token = auth.get('token')
    if (kind != 'bearer' or not isinstance(token, str) or not 0 < len(token) <= 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in token) or urlsplit(peer['url']).scheme != 'https'):
        raise ValueError('A2A bearer authentication requires an existing bounded token and HTTPS')
    return _auth_header(auth)


def _lane(database, config, entry):
    from hermes_cli.kanban_a2a_executor import A2AExecutor, PeerPlan
    from plugins.platforms.a2a.client import JSONRPCClient

    peer = _peer(config, entry)
    plan = PeerPlan(_identity(entry['executor_id']), peer['url'], tenant=peer.get('tenant', ''),
                    authority_id=_identity(entry['authority_id']))
    client = JSONRPCClient(plan.url, version=plan.version, headers=_headers(peer))
    return Lane(plan.executor_id, A2AExecutor(database, plan, client))


def configured_lanes(conn):
    """Read the owning profile only; never inherit root peers, discover or connect."""
    from hermes_cli.config import load_config_readonly

    config = load_config_readonly() or {}
    section = config.get('kanban') or {}
    setting = section.get('external_lanes') if isinstance(section, dict) else None
    if setting is None or setting == {}:
        return None
    if not isinstance(setting, dict) or type(setting.get('enabled', False)) is not bool:
        raise ValueError('External lanes require an explicit boolean enabled flag')
    if not setting.get('enabled', False):
        return None
    entries = setting.get('lanes')
    if (set(setting) != {'enabled', 'lanes'} or not isinstance(entries, dict)
            or not 0 < len(entries) <= _MAX_LANES):
        raise ValueError('Enabled external lanes require one to four explicit lane entries')
    row = conn.execute('PRAGMA database_list').fetchone()
    database = Path(row['file']).resolve()
    return ConfiguredLanes({name: _lane(database, config, entry) for name, entry in entries.items()})


def prepare_dispatch_lanes(conn, spawn_fn, dry_run, task_ids):
    if spawn_fn is not None:
        return None
    registry = configured_lanes(conn)
    if registry is not None and not dry_run:
        registry.reserve_refresh(conn, task_ids)
    return registry


def _mark(conn, task_id, run_id, kind, payload):
    with kb.write_txn(conn):
        kb._append_event(conn, task_id, kind, payload, run_id=run_id)


class ConfiguredLanes(ExternalLanes):
    """Poll at most four current bound runs, at most once per run per minute."""
    def __init__(self, lanes):
        super().__init__(lanes)
        self._sends = []
        self._refreshes = []

    def _submit(self, conn, delivery, lane):
        # The durable external_intent already consumes this attempt. A caller
        # crash before finish is uncertainty, never permission to submit again.
        self._sends.append((delivery, lane))

    def reserve_refresh(self, conn, task_ids=None):
        names = tuple(self._lanes)
        params = [*names, time.time() - _REFRESH_SECONDS]
        selection = ''
        if task_ids is not None:
            selection = f" AND t.id IN ({','.join('?' for _ in task_ids)})"
            params.extend(task_ids)
        rows = conn.execute(
            "SELECT e.task_id,e.run_id,e.payload,COALESCE((SELECT MAX(p.created_at) FROM task_events p "
            "WHERE p.task_id=e.task_id AND p.run_id=e.run_id AND p.kind='external_a2a_poll'),0) AS polled "
            "FROM task_events e JOIN tasks t ON t.id=e.task_id AND t.current_run_id=e.run_id "
            "WHERE e.kind='external_intent' AND t.status='running' AND t.claim_lock IS NOT NULL "
            f"AND t.assignee IN ({','.join('?' for _ in names)}) AND polled < ?" + selection +
            " ORDER BY polled,e.id LIMIT 4", params).fetchall()
        for row in rows:
            _mark(conn, row['task_id'], row['run_id'], 'external_a2a_poll', {})
            try:
                self._refreshes.append((row['task_id'], row['run_id'], Delivery(**json.loads(row['payload']))))
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
                # Configuration/receipt problems hold the run; no fallback or replay.
                _mark(conn, row['task_id'], row['run_id'], 'external_a2a_refresh_hold',
                      {'error_class': type(error).__name__})

    def finish(self, conn):
        """Peer I/O and lifecycle APIs run outside dispatcher admission locks."""
        sends, self._sends = self._sends, []
        refreshes, self._refreshes = self._refreshes, []
        for delivery, lane in sends:
            self._finish_one(conn, delivery.task_id, delivery.run_id, delivery, lane)
        for task_id, run_id, delivery in refreshes:
            self._finish_one(conn, task_id, run_id, delivery)

    def _finish_one(self, conn, task_id, run_id, delivery, lane=None):
        try:
            if lane is None:
                self._refresh_one(conn, delivery)
            else:
                super()._submit(conn, delivery, lane)
                self._attention(conn, delivery, lane.executor.status(delivery))
        except Exception as error:  # health: allow BLE001 -- isolate per-task callback failures without exposing untrusted exception text or abandoning other prepared sends
            _mark(conn, task_id, run_id, 'external_a2a_processing_hold',
                  {'error_class': type(error).__name__})

    def _attention(self, conn, delivery, status):
        from plugins.platforms.a2a.protocol import STATE_INPUT_REQUIRED

        if status is None or status.state != STATE_INPUT_REQUIRED:
            return False
        reason = 'External peer requires input: ' + ('; '.join(status.questions) or 'No validated question supplied')
        artifact = Artifact(delivery, 'question', reason, questions=status.questions)
        with kb.write_txn(conn):
            self._binding(conn, delivery)
            if not _owns(kb.get_task(conn, delivery.task_id), delivery):
                raise ValueError('External question ownership changed')
            previous = event(conn, delivery, 'external_a2a_attention')
            if previous is not None and previous != envelope(artifact):
                raise ValueError('Conflicting external question')
            if previous is None:
                kb._append_event(conn, delivery.task_id, 'external_a2a_attention', envelope(artifact), run_id=delivery.run_id)
        # Deliberately no external_terminal: the peer remains resumable, so the
        # unresolved intent still forbids a resend after this visible local hold.
        return kb.block_task(conn, delivery.task_id, reason=reason, kind='needs_input', expected_run_id=delivery.run_id)

    def _refresh_one(self, conn, delivery):
        if not self.registered(delivery.lane):
            raise ValueError('External lane no longer registered')
        if self.reconcile(conn, delivery):
            return
        executor = self._lanes[delivery.lane].executor
        status = executor.status(delivery)
        if self._attention(conn, delivery, status):
            return
        if status is not None and status.task_id is not None:
            executor.refresh_once(delivery)
            if not self.reconcile(conn, delivery):
                self._attention(conn, delivery, executor.status(delivery))
