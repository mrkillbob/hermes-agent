"""Compile the optional claim budget into the owner's frozen execution policy."""
from hermes_cli.kanban_execution_budget import validate_budget
from hermes_state_runtime import RuntimeStoreError


def validate_restriction(params):
    if params.get('execution_budget') is None:
        return False
    try:
        budget = validate_budget(params['execution_budget'])
    except ValueError as exc:
        raise RuntimeStoreError('invalid_params') from exc
    restricted = budget.get('local_text_only') is True
    if restricted and not params.get('route'):
        raise RuntimeStoreError('invalid_params')
    return restricted


def policy_inputs(task, restricted):
    params = {'source': 'cli', 'cwd': task.workspace_path}
    if restricted:
        if task.goal_mode:
            raise RuntimeStoreError('invalid_params')
        params.update(safe_mode=True, toolsets=[], max_turns=1)
    return params


def worker_toolsets(policy, route, restricted):
    if restricted:
        return ()
    toolsets = set(policy.toolsets) | {'kanban'}
    if route:
        toolsets.discard('code_execution')
    return tuple(sorted(toolsets))


def build_claim_policy(params, config, private, restricted):
    if restricted:
        from gateway.session_local import _bypass_policy
        return _bypass_policy(params, private_secrets=private)
    from gateway.session_policy import build_policy
    return build_policy(params, config, private_secrets=private)


def claim_config(params, loader):
    return {} if validate_restriction(params) else loader()
