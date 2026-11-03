"""Original armed-reference execution via real TurnRunner/AIAgent methods.

Only the detached private fork sees captured context; ordinary session state,
prompt prefix and tool schemas remain untouched. No alternative model fallback.
"""
import asyncio
import copy
from threading import current_thread

from gateway.session_learning_execution import original_execution, _deny


class ReferenceExecutionUnknown(RuntimeError):
    def __init__(self):
        super().__init__('reference_execution_unknown')


def execution_unknown(error):
    from gateway.session_managed_worker import ManagedExecutionUnknown
    return isinstance(error, (ManagedExecutionUnknown, ReferenceExecutionUnknown))


def _run(execution):
    from agent.background_review import build_cache_parity_fork
    from agent.diagnostic_reference_policy import DiagnosticPolicy, bind_agent, _private_output_scope
    from agent.reference_input_witness import ReferenceInputWitness, configure_private_transport
    from gateway.run_turn_runner import TurnRunner
    from gateway.turn_context import TurnContext
    from gateway.session_learning_payload import reference_context

    fork = policy = None
    ledger = execution.authority._side_agents
    thread = current_thread()
    holder = ledger[thread] = [None]
    try:
        execution.current()
        fork, _, routed = build_cache_parity_fork(execution.parent, {}, max_iterations=1,
                                                write_origin='curated_reference')
        if routed or (fork.provider, fork.model, fork.api_mode, fork.base_url) != execution.runtime:
            _deny()
        fork._disable_streaming = True
        fork.skip_background_review = True
        fork._native_send_operation = execution.native_send
        fork._reference_input_witness = ReferenceInputWitness(execution, execution.native_send)
        execution.witness = fork._reference_input_witness
        policy = DiagnosticPolicy('local-reference:' + execution.decision_id, '{}', fork.session_id)
        bind_agent(fork, policy)
        configure_private_transport(fork, fork._reference_input_witness)
        holder[0] = fork
        ctx = TurnContext(source=execution.source, session_id=fork.session_id,
            session_key=execution.route, message=reference_context(execution.selected.reference))
        with _private_output_scope(fork, policy):
            result = TurnRunner(execution.authority.runner, ctx)._run_conversation_with_approval(
                fork, copy.deepcopy(execution.history), '', None, None)
        if result.get('failed') or result.get('interrupted'):
            _deny('reference_outcome_unknown')
        execution.native_send.retire_and_wait()
        metadata = fork._reference_input_witness.metadata()
        with execution.broker._gate:
            execution.current()
            execution.proof = {'result': {'final_response': '', 'messages': [],
                'curated_input_provenance': metadata}, 'usage': {}}
            execution.status = 'observed'
        return execution.proof
    except Exception:  # health: allow BLE001 -- Never expose captured input through construction, provider or cleanup exceptions.
        execution.status = 'unknown'
        raise ReferenceExecutionUnknown() from None
    finally:
        _dispose(execution, fork, policy, ledger, thread)


def _dispose(execution, fork, policy, ledger, thread):
    from agent.diagnostic_reference_policy import _private_output_scope
    try:
        # A timed-out facade or cancelled awaiter does not end the SDK thread.
        # Retire prepared entry, or join the real operation's cleanup event.
        execution.native_send.retire_and_wait()
        if fork is not None and policy is not None:
            with _private_output_scope(fork, policy):
                try:
                    fork.release_clients()
                finally:
                    fork._reference_input_witness.transport.close()
        elif fork is not None:
            fork.release_clients()
    except Exception:  # health: allow BLE001 -- Cleanup failures must not export captured context in error output.
        execution.status = 'unknown'
        raise ReferenceExecutionUnknown() from None
    finally:
        execution.selected.reference, execution.history = None, []
        if fork is not None:
            witness = fork.__dict__.pop('_reference_input_witness', None)
            if witness is not None:
                witness.request = None
        ledger.pop(thread, None)
        if execution.status != 'observed':
            from gateway.session_learning_cache import release_pin
            release_pin(execution)


async def execute_reference(authority, ref, row):
    execution = original_execution(authority, row)
    execution.current()
    if execution.status != 'claimed' or execution.generation != row['generation']:
        _deny()
    execution.status = 'executing'
    execution.native_send = execution.selected.connection._native_commit_issuer.issue_send(
        operation_id='curated-send:' + execution.decision_id,
        payload_sha256=execution.selected.reference.payload_sha256)
    if not hasattr(authority, '_side_agents'):
        authority._side_agents = {}
    from gateway.session_runtime_workers import track_mutation
    worker = track_mutation(authority, asyncio.to_thread(_run, execution))
    worker.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        result = await asyncio.shield(worker)
    except BaseException:
        with execution.broker._gate:
            execution.status = 'unknown'
        from gateway.session_learning_cache import release_pin
        if worker.done():
            release_pin(execution)
        # Metadata-only owner uncertainty is not note access or a forged result.
        from gateway.session_settlement_recovery import recover_failed_settlement
        await recover_failed_settlement(authority, row)
        raise
    authority.pending_results[row['admission_id']] = result
