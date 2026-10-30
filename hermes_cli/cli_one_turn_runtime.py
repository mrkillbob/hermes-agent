"""Keep local one-turn restoration ownership across a gateway proxy rebuild."""


def remember_gateway_one_turn_controller(cli):
    """Retain provenance before retirement, including across refused initialization attempts."""
    from hermes_cli.gateway_agent import GatewayAgent

    snapshot = getattr(cli, "_pending_one_turn_model_restore", None)
    previous = getattr(cli, "_one_turn_gateway_controller_owner", None)
    if previous is not None and previous[0] is snapshot:
        return
    cli._one_turn_gateway_controller_owner = None
    if snapshot is not None and isinstance(cli.agent, GatewayAgent):
        cli._one_turn_gateway_controller_owner = (snapshot, cli.agent)


def carry_gateway_one_turn_controller(cli):
    """A same-session proxy rebuild is not a newer successful model selection."""
    from agent.primary_runtime_snapshot import RuntimeRecoveryPendingError
    from agent.runtime_mutation import RuntimeMutationState, _INITIALIZE_MUTEX
    from hermes_cli.gateway_agent import GatewayAgent

    previous = getattr(cli, "_one_turn_gateway_controller_owner", None)
    if previous is None:
        return
    snapshot, previous_agent = previous
    agent = cli.agent
    if (snapshot is not getattr(cli, "_pending_one_turn_model_restore", None)
            or previous_agent is agent or not isinstance(agent, GatewayAgent)):
        return
    state = vars(previous_agent).get("_runtime_mutation_state")
    if not isinstance(state, RuntimeMutationState):
        return
    # Serialize with lazy controller creation; never overwrite a controller another caller owns.
    with _INITIALIZE_MUTEX, state.mutex:
        if snapshot.get("_runtime_selection_token") != state.selection_token:
            return  # A successful competing selection already superseded this receipt.
        if (agent.connection is not previous_agent.connection or agent.session_id != previous_agent.session_id
                or vars(agent).get("_runtime_mutation_state") not in (None, state) or state.busy_locked):
            raise RuntimeRecoveryPendingError("one-turn gateway restoration ownership changed during rebuild")
        agent._runtime_mutation_state = state
