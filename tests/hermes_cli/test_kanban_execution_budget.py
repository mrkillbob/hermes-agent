"""An opt-in execution ceiling is closed, finite, and tied to one exact card."""
import pytest


BUDGET = {'wall_seconds': 2, 'output_bytes': 1024, 'requests': 1}


@pytest.mark.parametrize('value', [None, {}, BUDGET | {'requests': True},
    BUDGET | {'wall_seconds': 121}, BUDGET | {'output_bytes': 0},
    BUDGET | {'requests': 5}, BUDGET | {'extra': 1}])
def test_invalid_budget_is_rejected(value):
    from hermes_cli.kanban_execution_budget import validate_budget
    with pytest.raises(ValueError):
        validate_budget(value)


def test_budget_copied_and_retry_cannot_remove_or_replace_it():
    from hermes_cli.kanban_execution_budget import validate_budget
    from gateway.session_kanban import retry_matches_frozen_claim
    assert validate_budget(BUDGET) == BUDGET
    claim = dict(board='default', task_id='t_a', run_id=1, claim_lock='owned')
    frozen = claim | {'execution_budget': BUDGET}
    assert retry_matches_frozen_claim(frozen, frozen)
    assert not retry_matches_frozen_claim(claim, frozen)
    assert not retry_matches_frozen_claim(claim | {'execution_budget': BUDGET | {'requests': True}}, frozen)
    assert not retry_matches_frozen_claim(claim | {'execution_budget': BUDGET | {'requests': 2}}, frozen)


@pytest.mark.parametrize('ids,maximum', [(None, 1), (['t_a', 't_b'], 1), (['t_a'], None), (['t_a'], 2)])
def test_budget_requires_single_exact_selection_before_database_access(ids, maximum):
    from hermes_cli.kanban_db_dispatch import dispatch_once
    with pytest.raises(ValueError):
        dispatch_once(None, task_ids=ids, max_spawn=maximum, execution_budget=BUDGET)


def test_bounded_worker_never_starts_a_missing_owner(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from hermes_cli import gateway_client, gateway_runtime, kanban_worker_client
    monkeypatch.delenv('HERMES_TUI_GATEWAY_URL', raising=False)
    monkeypatch.setattr(gateway_runtime, 'discover_gateway_endpoint',
                        lambda home: SimpleNamespace(state='missing', endpoint=None))

    def forbidden(**kwargs):
        pytest.fail('bounded worker tried to start/connect an unprepared owner')
    monkeypatch.setattr(gateway_client, 'connect_gateway', forbidden)
    with pytest.raises(gateway_client.GatewayUnavailableError, match='existing ready owner'):
        asyncio.run(kanban_worker_client.run({'execution_budget': BUDGET}, '/unused'))


def test_worker_client_carries_route_and_budget_together(monkeypatch, tmp_path):
    import json
    from hermes_cli import kanban_worker_client
    for key, value in {'BOARD': 'default', 'TASK': 't_a', 'RUN_ID': '1',
                       'CLAIM_LOCK': 'owned', 'DB': str(tmp_path / 'board.db')}.items():
        monkeypatch.setenv('HERMES_KANBAN_' + key, value)
    seen = []

    async def run(params, database):
        seen.append((params, database))
        return 0
    monkeypatch.setattr(kanban_worker_client, 'run', run)
    route = {'provider': 'custom', 'model': 'fixture'}
    assert kanban_worker_client._submit(['--route', json.dumps(route), '--budget', json.dumps(BUDGET)]) == 0
    assert seen[0][0]['execution_budget'] == BUDGET and seen[0][0]['route'] == route


def test_late_owner_budget_refusal_dominates_early_success_event(monkeypatch, tmp_path):
    import asyncio
    from hermes_cli import kanban_worker_client
    from gateway import session_kanban
    monkeypatch.setattr(session_kanban, 'worker_result', lambda path, params: {'exit_code': 0})
    reads = []

    class Client:
        async def rpc(self, method, **params):
            if method == 'kanban.run':
                return {'session_id': 's', 'receipt': {'admission_id': 'a', 'status': 'terminal'}}
            reads.append(params)
            return {'status': 'terminal', 'result': {'completed': False, 'interrupted': True,
                'failed': True, 'turn_exit_reason': 'worker_request_budget_exhausted'}}
    assert asyncio.run(kanban_worker_client._submit_claim(Client(), {'execution_budget': BUDGET},
                                                       str(tmp_path / 'board.db'))) == 130
    assert reads == [{'session_id': 's', 'admission_id': 'a', 'include_result': True}]
