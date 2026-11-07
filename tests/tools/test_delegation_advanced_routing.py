from __future__ import annotations

from typing import Any

from agent.delegation_provider import DelegationRouteError
from plugins.delegation.advanced_routing import AdvancedRoutingProvider


def _cfg(settings: dict[str, Any]) -> dict[str, Any]:
    return {"backend": "advanced-routing", "backends": {"advanced-routing": settings}}


def test_schema_declares_task_class() -> None:
    p = AdvancedRoutingProvider()
    params = p.task_parameters(_cfg({"classes": {"analysis": {}, "code": {}}}))
    assert "task_class" in params
    assert set(params["task_class"].get("enum", [])) == {"analysis", "code"}


def test_budget_allows_and_refuses_with_fallback() -> None:
    p = AdvancedRoutingProvider()
    settings = {
        "classes": {
            "analysis": {"provider": "openrouter", "model": "thinkingmachines/inkling-small:free"},
        },
        "allowances": {
            "openrouter:thinkingmachines/inkling-small:free": {"remaining": 0}
        },
        "global_fallbacks": [
            {"provider": "llamacpp", "model": "Devstral-Small-2-24B-Instruct-2512-Q4_K_M", "reasoning_effort": "none"}
        ],
    }
    cfg = _cfg(settings)
    # Exhausted: should pick fallback (local llamacpp)
    route = p.route_task({"goal": "analyze doc", "task_class": "analysis"}, cfg)
    assert route.get("provider") == "llamacpp"

    # Allow primary when remaining>0
    settings["allowances"]["openrouter:thinkingmachines/inkling-small:free"]["remaining"] = 1
    route = p.route_task({"goal": "analyze doc", "task_class": "analysis"}, _cfg(settings))
    assert route.get("provider") == "openrouter"


def test_budget_exhausted_without_fallback_refuses() -> None:
    p = AdvancedRoutingProvider()
    settings = {
        "classes": {
            "code": {"provider": "openrouter", "model": "thinkingmachines/inkling-small:free"},
        },
        "allowances": {
            "openrouter:thinkingmachines/inkling-small:free": {"remaining": 0}
        },
    }
    try:
        p.route_task({"goal": "fix bug", "task_class": "code"}, _cfg(settings))
        assert False, "expected refusal"
    except DelegationRouteError as e:
        assert "Budget exhausted" in str(e)
