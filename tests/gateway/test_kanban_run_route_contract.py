"""The native kanban.run contract must admit the route the dispatcher's worker client sends."""

from tui_gateway.contracts.registry import CANONICAL_METHODS, canonical_param_problems

_CLAIM = {"board": "default", "task_id": "t_1", "run_id": 1, "claim_lock": "lock"}


def test_kanban_run_accepts_the_local_route():
    route = {"provider": "ollama", "model": "qwen3.6:35b-a3b", "reasoning": "none",
             "base_url": "http://127.0.0.1:11434/v1"}
    contract = CANONICAL_METHODS["kanban.run"]
    assert canonical_param_problems(contract, {**_CLAIM, "route": route}) == []
    assert canonical_param_problems(contract, {**_CLAIM, "route": {"bogus": "x"}}) == ["route.bogus"]


def test_kanban_run_accepts_closed_execution_budget():
    contract = CANONICAL_METHODS["kanban.run"]
    budget = {"wall_seconds": 2, "output_bytes": 1024, "requests": 1}
    assert canonical_param_problems(contract, {**_CLAIM, "execution_budget": budget}) == []
    assert canonical_param_problems(contract, {**_CLAIM, "execution_budget": budget | {"extra": 1}}) == ["execution_budget.extra"]
