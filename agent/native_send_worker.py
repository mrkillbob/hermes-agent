"""Private HOST-supplied transport hold for the qualified standard SDK worker.

No note/session permission, final-input proof, destination authorization or ACK.
No RPC/config/request field binds this object. Other provider paths refuse until
their physical completion/cleanup and retry behavior are separately qualified.
"""
from agent.local_text_policy import LocalTextRestriction


def capture_native_send(agent):
    operation = getattr(agent, '__dict__', {}).get('_native_send_operation')
    if operation is None:
        return None
    from agent.diagnostic_reference_policy import private_output_owner
    from gateway.session_native_commit import NativeSendOperation
    from agent.chat_completion_helpers import should_use_direct_api_call
    if (type(operation) is not NativeSendOperation or not private_output_owner(agent)
            or operation.state != 'prepared' or getattr(agent, '_disable_streaming', False) is not True
            or agent.api_mode != 'chat_completions' or agent.provider == 'moa'
            or should_use_direct_api_call(agent)):
        raise LocalTextRestriction('native_send_worker_unavailable')
    return operation


def validate_send_client(operation, client, kwargs):
    if operation is None:
        return
    from openai import OpenAI
    extra = kwargs.get('extra_body')
    if (type(client) is not OpenAI or type(client.max_retries) is not int or client.max_retries != 0
            or (kwargs.get('stream') is not None and kwargs.get('stream') is not False)
            or (isinstance(extra, dict) and extra.get('stream') is not None and extra.get('stream') is not False)):
        raise LocalTextRestriction('native_send_client_unavailable')
