"""One private request's serialized-input and HTTP-response observations.

No note body, response body or arbitrary exception enters a receipt. A successful
local HTTP observation is NOT semantic consumption or session acknowledgment.
"""
import hashlib
import json
from contextlib import contextmanager

from agent.local_text_policy import LocalTextRestriction


class ReferenceInputWitness:
    def __init__(self, execution, operation):
        self.execution, self.operation = execution, operation
        self.request = self.request_sha = self.status = self.sdk_response_sha = None
        self.transport = None

    def request_hook(self, request):
        self.execution.current()
        s = self.execution.selected
        expected = self.execution.intent['destination'] + '/chat/completions'
        if (self.request is not None or str(request.url) != expected or request.method != 'POST'
                or len(request.content) > 4 * 1024 * 1024):
            raise LocalTextRestriction('reference_input_unavailable')
        payload = json.loads(request.content)
        from gateway.session_learning_payload import reference_context
        messages = payload.get('messages')
        if (payload.get('model') != self.execution.intent['model'] or payload.get('stream', False)
                or type(messages) is not list or not messages
                or messages[-1].get('role') != 'user'
                or messages[-1].get('content') != reference_context(s.reference)):
            raise LocalTextRestriction('reference_input_unavailable')
        self.request, self.request_sha = request, hashlib.sha256(request.content).hexdigest()

    def response_hook(self, response):
        if self.request is None or response.request is not self.request or self.status is not None:
            raise LocalTextRestriction('reference_result_unavailable')
        self.status = response.status_code

    def metadata(self):
        self.execution.current()
        if (self.request is None or self.status != 200 or self.sdk_response_sha is None
                or self.operation.state != 'released'
                or not self.operation.lease_released):
            raise LocalTextRestriction('reference_result_unavailable')
        s = self.execution.selected
        return {**s.reference.metadata(), 'schema': 'curated-local-input-observation-v1',
            'admission_id': s.admission_id, 'execution_generation': self.execution.generation,
            'principal_id': s.fence.principal_id, 'transport_id': s.fence.transport_id,
            'logical_session_id': s.fence.logical_session_id, 'physical_target': s.fence.physical_target,
            'source_sha': s.fence.source_sha, 'authority_epoch': s.fence.authority_epoch,
            'intent_sha256': self.execution.intent_digest, 'serialized_request_sha256': self.request_sha,
            'normalized_sdk_response_sha256': self.sdk_response_sha,
            'serialized_input_observed': True, 'http_status': self.status,
            'http_response_observed': True, 'session_acknowledged': False,
            'semantic_consumption_attested': False, 'distributed_fence': False}


def install_private_client(witness, client):
    if witness is None:
        return
    if type(witness) is not ReferenceInputWitness:
        raise LocalTextRestriction('reference_input_unavailable')
    if client._client is not witness.transport:
        raise LocalTextRestriction('reference_transport_unavailable')


def configure_private_transport(agent, witness):
    """HOST-owned detached fork only; retain the normal SDK/egress factory path."""
    import httpx
    witness.transport = httpx.Client(trust_env=False, follow_redirects=False,
        event_hooks={'request': [witness.request_hook], 'response': [witness.response_hook]})
    agent._client_kwargs = {**agent._client_kwargs, 'http_client': witness.transport}


def observe_private_response(witness, response):
    if witness is None:
        return
    from openai.types.chat import ChatCompletion
    if (type(witness) is not ReferenceInputWitness or type(response) is not ChatCompletion
            or witness.request is None or witness.sdk_response_sha is not None):
        raise LocalTextRestriction('reference_result_unavailable')
    serialized = response.model_dump_json().encode('utf-8')
    if len(serialized) > 4 * 1024 * 1024:
        raise LocalTextRestriction('reference_result_unavailable')
    witness.sdk_response_sha = hashlib.sha256(serialized).hexdigest()


def capture_reference_input(agent, operation):
    witness = agent.__dict__.get('_reference_input_witness')
    if witness is not None and (type(witness) is not ReferenceInputWitness
            or witness.operation is not operation or witness.execution.witness is not witness):
        raise LocalTextRestriction('reference_input_unavailable')
    return witness


@contextmanager
def reference_worker_scope(witness):
    if witness is None:
        yield
    else:
        with witness.execution.physical_scope():
            yield
