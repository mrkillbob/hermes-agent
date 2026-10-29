"""Host-selected, one-use exact-reference decisions for an existing native session.

SOURCE CANDIDATE: no RPC registration or production issuer is installed here. The
host calls select outside request/model/note data; a native reply can only decide
that already selected record. The actual verified connection remains the authority
root. Constructing Python objects, including AuthorityConnection, is not transport
authentication and is outside the trusted-in-process boundary.

Do not use this candidate for live delivery until connection invalidation and the
owner's admission/delivery gates share the reviewed atomic fence. Per-call checks
alone are insufficient for that qualification. No Codex conversation binding or
desktop activity authority is issued by this component.
"""
from dataclasses import dataclass, replace
import re
import secrets
import threading
import time

from agent.learning_vault import ExactVaultNoteRequest, read_exact_vault_learning
from agent.learning_vault_exact import _valid_request
from gateway.session_contract import SessionRef
from gateway.session_learning_payload import CapturedReference
from hermes_state_runtime import RuntimeStoreError


def _deny(reason='permission_denied'):
    raise RuntimeStoreError(reason)


def _identifier(value):
    return (type(value) is str and 0 < len(value) <= 128
            and all(32 < ord(char) < 127 for char in value))


@dataclass(frozen=True)
class HostFence:
    principal_id: str
    transport_id: str
    profile_id: str
    instance_id: str
    source_sha: str
    authority_epoch: int
    logical_session_id: str
    physical_target: str
    revision: int
    selection_generation: int


@dataclass
class _Decision:
    connection: object
    live: object
    fence: HostFence
    request_id: str
    exact: ExactVaultNoteRequest
    expires_at: float
    challenge: str
    status: str = 'pending'
    reference: CapturedReference | None = None
    admission_id: str | None = None
    delivery_generation: int | None = None


class LearningBroker:
    """One owner instance, default empty. All state is volatile; restart refuses reuse.

    A registry entry is provenance, never standalone permission. Every operation
    rechecks the exact native connection, live object, current owner and source.
    Source selection/decision/expiry cannot come from a remote read request.
    """
    def __init__(self, authority):
        self.authority = authority
        self._decisions = {}
        self._gate = threading.RLock()

    def _source_sha(self):
        from hermes_cli.version_info import get_code_identity
        identity = get_code_identity()
        sha = identity.get('sha')
        if type(sha) is not str or re.fullmatch(r'[0-9a-f]{40}', sha) is None:
            _deny('source_unavailable')
        # Install metadata is the existing source authority, not evidence of adoption.
        return sha

    def _native_live(self, connection, ref, *, admission=True):
        from gateway.config import Platform
        from gateway.session_authorities import require_serving
        from gateway.session_controls import AuthorityConnection
        authority = self.authority
        require_serving(authority)
        if (type(connection) is not AuthorityConnection or connection.authority is not authority
                or connection.native_owner is not True or type(ref) is not SessionRef
                or ref.profile_id != authority.profile_id):
            _deny()
        actor = connection.actor
        identity = connection._identity
        if (actor.profile_id != authority.profile_id or actor.subject == 'api'
                or not {'session:read', 'session:submit', 'session:approve'} <= actor.capabilities
                or identity.get('provider') != 'local'
                or identity.get('instance_id') != authority.instance_id
                or identity.get('profile_id') != authority.profile_id
                or getattr(authority, '_native_legacy_transports', {}).get(actor.transport_id) is not actor
                or authority.events.get(actor.transport_id) is not connection.transport):
            _deny()
        live = authority.sessions.get(ref.session_id)
        if (live is None or live.source is None or live.source.platform != Platform.LOCAL
                or live.source.user_id != actor.subject
                or not any(member is actor for member in live.subscribers.values())):
            _deny()
        if admission:
            authority._require_admission_open(session_id=ref.session_id)
        return live

    def _fence(self, connection, ref):
        live = self._native_live(connection, ref)
        handle = self.authority._handle(ref)
        if handle.execution_state != 'idle' or self.authority._pending_rows(ref):
            _deny('session_busy')
        if self.authority.logical_owner(ref.session_id) != ref.session_id:
            _deny('stale_generation')
        fence = HostFence(
            connection.actor.subject, connection.actor.transport_id, ref.profile_id,
            self.authority.instance_id, self._source_sha(), self.authority.epoch,
            ref.session_id, self.authority.physical_target(ref), handle.revision,
            handle.execution_generation)
        return live, fence

    def select(self, connection, ref, *, request_id, exact, lifetime_s=60):
        """TRUSTED HOST ONLY, never an RPC. No path is opened by this method.

        The application/operator must independently select root/path/ID/hash/limit
        outside incoming params, note/model text and remote callbacks. A later
        native decision names only this opaque selection, not replacement fields.
        """
        if (not _identifier(request_id) or type(lifetime_s) is not int
                or not 1 <= lifetime_s <= 120 or not _valid_request(exact)):
            _deny('invalid_params')
        snapshot = replace(exact)
        with self._gate:
            # Volatile decisions expire, including their bounded captured context.
            # Removing an expired opaque ID cannot make it valid again.
            now = time.monotonic()
            self._decisions = {key: value for key, value in self._decisions.items()
                               if now < value.expires_at}
            if len(self._decisions) >= 64:
                _deny('session_busy')
            live, fence = self._fence(connection, ref)
            decision_id = secrets.token_hex(24)
            self._decisions[decision_id] = _Decision(
                connection, live, fence, request_id, snapshot,
                time.monotonic() + lifetime_s, secrets.token_hex(24))
            return decision_id

    def _selected(self, connection, decision_id, request_id):
        if not _identifier(decision_id) or not _identifier(request_id):
            _deny('invalid_params')
        selected = self._decisions.get(decision_id)
        if (selected is None or selected.connection is not connection
                or selected.request_id != request_id):
            _deny()
        if time.monotonic() >= selected.expires_at:
            selected.status = 'expired'
            _deny()
        if selected.status in {'revoked', 'expired', 'refused', 'uncertain'}:
            _deny()
        return selected

    def _unchanged(self, selected):
        if time.monotonic() >= selected.expires_at:
            selected.status = 'expired'
            _deny()
        ref = SessionRef(selected.fence.profile_id, selected.fence.logical_session_id)
        live, fence = self._fence(selected.connection, ref)
        if live is not selected.live or fence != selected.fence:
            _deny('stale_generation')

    def decide(self, connection, *, decision_id, request_id, choice):
        """Native decision carrier; it cannot create or alter a host selection."""
        if choice not in ('once', 'deny') or type(choice) is not str:
            _deny('invalid_params')
        with self._gate:
            selected = self._selected(connection, decision_id, request_id)
            self._unchanged(selected)
            if selected.status != 'pending':
                _deny()
            selected.status = 'approved' if choice == 'once' else 'revoked'
            return {'decision_id': decision_id, 'status': selected.status}

    def revoke(self, connection, *, decision_id, request_id):
        """Revokes future capture/admission/ack. Does not recall already delivered bytes."""
        with self._gate:
            selected = self._selected(connection, decision_id, request_id)
            ref = SessionRef(selected.fence.profile_id, selected.fence.logical_session_id)
            self._native_live(connection, ref, admission=False)
            selected.status = 'revoked'

    def capture(self, connection, *, decision_id, request_id):
        """One library read of the host-selected buffer, without scan or fallback.

        Source candidate only: this is not session admission or delivery. It is
        intentionally not registered as a production method until the common gate
        with connection close and owner admission is implemented and reviewed.
        """
        with self._gate:
            selected = self._selected(connection, decision_id, request_id)
            self._unchanged(selected)
            if selected.status != 'approved':
                _deny()
            selected.status = 'reading'
            def authorize(request):
                self._unchanged(selected)
                return request == selected.exact and selected.status == 'reading'
            try:
                node, diagnostic = read_exact_vault_learning(selected.exact, authorize=authorize)
                if diagnostic is not None or node is None:
                    selected.status = 'refused'
                    _deny('reference_refused')
                self._unchanged(selected)
                reference = CapturedReference.from_node(
                    node, raw_sha256=selected.exact.expected_sha256)
                self._unchanged(selected)
                if selected.status != 'reading':
                    _deny()
                selected.reference = reference
            except RuntimeStoreError:
                if selected.status == 'reading':
                    selected.status = 'uncertain'
                raise
            except Exception:  # health: allow BLE001 -- Bound capture failures without disclosing note data or arbitrary exception text.
                # Stable refusal only; no path, body, or arbitrary exception text.
                if selected.status == 'reading':
                    selected.status = 'uncertain'
                raise RuntimeStoreError('reference_unavailable') from None
            except BaseException:
                # Any uncertain I/O outcome consumes this attempt; no automatic retry.
                if selected.status == 'reading':
                    selected.status = 'uncertain'
                raise
            selected.status = 'captured'
            return self._metadata(decision_id, selected)

    def _metadata(self, decision_id, selected):
        from dataclasses import asdict
        return {'schema': 'curated-host-decision-v1', 'decision_id': decision_id,
                'request_id': selected.request_id, 'status': selected.status,
                **asdict(selected.fence),
                **(selected.reference.metadata() if selected.reference is not None else {}),
                'session_delivery': False, 'session_acknowledged': False,
                'codex_conversation_binding': 'unavailable', 'activity_authority': 'unavailable'}
