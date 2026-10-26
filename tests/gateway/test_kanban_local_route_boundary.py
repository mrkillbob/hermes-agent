"""A routed local claim must not reach a remote/default endpoint at the owner."""
from types import SimpleNamespace

import pytest

from hermes_state_runtime import RuntimeStoreError


@pytest.mark.parametrize('provider,endpoint', [
    ('llamacpp', None), ('llamacpp', ''), ('llamacpp', 'https://provider.example/v1'),
    ('llamacpp', 'http://localhost.example/v1'), ('llamacpp', 'http://127.0.0.1.example/v1'),
    ('llamacpp', 'file:///tmp/model'), ('llamacpp', 'http://user:secret@localhost/v1'),
    ('llamacpp', 'http://localhost:bad/v1'), ('nous', 'http://localhost:8000/v1'),
])
def test_invalid_routed_endpoint_refuses_before_claim_read(tmp_path, monkeypatch, provider, endpoint):
    from gateway import session_kanban as owner
    path = tmp_path / 'claim.db'
    path.touch()
    connection = SimpleNamespace(native_owner=True,
        authority=SimpleNamespace(profile_id='profile'),
        actor=SimpleNamespace(profile_id='profile', capabilities={'session:create'}))
    params = {'board': 'default', 'task_id': 't_control', 'run_id': 1,
              'claim_lock': 'claim', 'db': str(path),
              'execution_budget': dict(wall_seconds=60, output_bytes=8192, requests=1, local_text_only=True),
              'route': {'provider': provider, 'model': 'public-model',
                        'reasoning': 'none', 'base_url': endpoint}}
    def must_not_open(*args, **kwargs):
        pytest.fail('claim DB must not open for rejected route')
    monkeypatch.setattr(owner.sqlite3, 'connect', must_not_open)
    assert not owner.retry_matches_frozen_claim(params, dict(params))
    assert not owner.retry_matches_frozen_claim({k: v for k, v in params.items() if k != 'route'}, dict(params))
    with pytest.raises(RuntimeStoreError, match='invalid_params'):
        owner.build_kanban_policy(connection, params, {})


@pytest.mark.parametrize('route', [
    {}, {'provider': 'llamacpp', 'model': 'public-model', 'reasoning': 'none', 'base_url': 'http://127.0.0.1:8000/v1'},
    {'provider': 'llamacpp', 'model': 'public-model', 'reasoning': 'none', 'base_url': None},
    {'provider': 'ollama', 'model': 'public-model', 'reasoning': None, 'base_url': 'http://localhost:11434'},
    {'provider': 'llama.cpp', 'model': 'public-model', 'reasoning': 'low', 'base_url': 'http://[::1]:8000/v1'},
])
def test_explicit_loopback_route_preserves_model_and_effort(route, tmp_path, monkeypatch):
    from gateway.session_kanban_route import validate_local_worker_route
    original = dict(route)
    if not route or route.get('base_url') is not None:
        validate_local_worker_route(route)
    assert route == original
    from contextlib import closing
    from gateway.session_kanban import build_kanban_policy, retry_matches_frozen_claim
    from gateway.session_contract import Principal
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    monkeypatch.setattr('hermes_cli.profiles.resolve_profile_env', lambda _: str(tmp_path))
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    kb.create_board('owned')
    with closing(connect(board='owned')) as conn:
        task_id = kb.create_task(conn, title='Public control', body='Public facts', assignee='default',
            workspace_kind='dir', workspace_path=str(workspace), model_override='public-model', provider_override='custom')
        kb.recompute_ready(conn)
        task = kb.claim_task(conn, task_id)
    actor = Principal('owner', str(tmp_path), frozenset({'session:create'}), 'transport')
    connection = SimpleNamespace(native_owner=True, actor=actor, authority=SimpleNamespace(profile_id=str(tmp_path)))
    params = dict(board='owned', task_id=task.id, run_id=task.current_run_id, claim_lock=task.claim_lock, route=route)
    policy, _ = build_kanban_policy(connection, params, {'model': {'default': 'public-model', 'provider': 'custom'}})
    assert policy.model == 'public-model'
    assert policy.provider == route.get('provider', 'custom')
    assert retry_matches_frozen_claim(params, dict(params))
    assert retry_matches_frozen_claim({k: v for k, v in params.items() if k != 'route'}, dict(params))
