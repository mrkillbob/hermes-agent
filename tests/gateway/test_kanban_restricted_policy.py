"""Restricted text policy travels in the existing immutable claim budget."""
from contextlib import closing
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest


def claim(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    from gateway.session_contract import Principal
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.setattr('hermes_cli.profiles.resolve_profile_env', lambda _: str(tmp_path))
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    kb.create_board('owned')
    with closing(connect(board='owned')) as conn:
        task_id = kb.create_task(conn, title='Public control', body='Public facts', assignee='default',
            workspace_kind='dir', workspace_path=str(workspace))
        kb.recompute_ready(conn); task = kb.claim_task(conn, task_id)
    actor = Principal('owner', str(tmp_path), frozenset({'session:create'}), 'transport')
    connection = SimpleNamespace(native_owner=True, actor=actor, authority=SimpleNamespace(profile_id=str(tmp_path)))
    params = dict(board='owned', task_id=task.id, run_id=task.current_run_id, claim_lock=task.claim_lock,
        route=dict(provider='llamacpp', model='public-model', reasoning='none', base_url='http://127.0.0.1:8000/v1'))
    return connection, params


@pytest.mark.parametrize('restricted', [False, True])
def test_frozen_owner_policy_and_retry_preserve_restriction(tmp_path, monkeypatch, restricted):
    from gateway.session_kanban import build_kanban_policy, retry_matches_frozen_claim
    from gateway.session_policy import restore_policy
    from gateway.session_worker_construct import construct_inputs, construct_kwargs
    from agent.local_text_policy import restricted_policy
    connection, params = claim(tmp_path, monkeypatch)
    budget = dict(wall_seconds=60, output_bytes=8192, requests=1)
    if restricted: budget['local_text_only'] = True
    params['execution_budget'] = budget
    owner_config = {'platform_toolsets': {'cli': ['terminal']},
        'providers': {'llamacpp': {'base_url':'http://127.0.0.1:8000/v1','key_cmd':'PRIVATE_AMBIENT_SENTINEL'}},
        'agent': {'system_prompt':'PRIVATE_AMBIENT_SENTINEL'}}
    policy, _ = build_kanban_policy(connection, params, owner_config)
    policy = restore_policy(asdict(policy))
    context = json.loads(policy.kanban_json)
    assert restricted_policy(policy) is restricted
    assert ('PRIVATE_AMBIENT_SENTINEL' not in policy.config_json) is restricted
    assert policy.safe_mode is restricted and policy.ignore_user_config is restricted
    assert policy.toolsets == () if restricted else 'terminal' in policy.toolsets
    assert context['execution_budget'] == budget
    assert json.loads(policy.request_json)['reasoning'] == 'none'
    assert retry_matches_frozen_claim(params, context)
    if restricted:
        from gateway.session_kanban_restriction import claim_config
        def forbidden_loader(): pytest.fail('owner configuration must not be read')
        assert claim_config(params, forbidden_loader) == {}
        assert not retry_matches_frozen_claim({**params, 'execution_budget': {k:v for k,v in budget.items() if k != 'local_text_only'}}, context)
        inputs = construct_inputs(None, policy, None)
        assert inputs == {'fallback_model': {}, 'ephemeral_system_prompt': None, 'prefill_messages': None}
        frame = {'policy': {**asdict(policy), 'request_json': json.dumps({'construct_v1': inputs})}}
        assert construct_kwargs(frame) == inputs
        frame['policy']['request_json'] = json.dumps({'construct_v1': {**inputs, 'fallback_model': [{'provider': 'nvidia','model': 'fixture'}]}})
        with pytest.raises(ValueError): construct_kwargs(frame)


@pytest.mark.parametrize('restricted', [False, True])
def test_worker_resolution_never_walks_restricted_fallback(tmp_path, monkeypatch, restricted):
    from gateway.session_kanban import build_kanban_policy
    from gateway import session_worker_construct as construct
    connection, params = claim(tmp_path, monkeypatch)
    if restricted: params['execution_budget'] = dict(wall_seconds=60, output_bytes=8192, requests=1, local_text_only=True)
    policy, _ = build_kanban_policy(connection, params, {})
    seen = []
    def runtime(*args, fallback=True):
        seen.append(fallback)
        return {'base_url': 'http://127.0.0.1:8000/v1', 'api_mode':'chat_completions','provider':'custom'}, None
    monkeypatch.setattr(construct, 'route_runtime', runtime)
    assert construct.worker_route(policy, {'api_key': None})['base_url'] == policy.base_url
    assert seen == ([] if restricted else [True])
    if restricted:
        from dataclasses import replace
        from hermes_state_runtime import RuntimeStoreError
        frozen = policy.config()
        frozen['model']['base_url'] = 'https://remote.invalid'
        changed = replace(policy, config_json=json.dumps(frozen))
        with pytest.raises(RuntimeStoreError, match='invalid_params'):
            construct.worker_route(changed, {'api_key':None})
