from __future__ import annotations

import json
from typing import Any, Mapping
from pathlib import Path

from agent.delegation_provider import DelegationProvider, DelegationRouteError


def _settings_block(config: Mapping[str, Any]) -> Mapping[str, Any]:
    backends = config.get("backends") if isinstance(config, Mapping) else None
    own = backends.get("advanced-routing") if isinstance(backends, Mapping) else None
    return own if isinstance(own, Mapping) else {}


def _read_usage_cache(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        p = Path(path).expanduser()
        if not p.is_file():
            return {}
        with p.open("r", encoding="utf-8-sig") as fh:
            return json.load(fh) or {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _allowance_key(route: Mapping[str, Any]) -> str:
    if route.get("profile"):
        return f"profile:{route.get('profile')}"
    prov = str(route.get("provider") or "").strip() or "custom"
    model = str(route.get("model") or "").strip()
    return f"{prov}:{model}" if model else prov


class AdvancedRoutingProvider(DelegationProvider):
    name = "advanced-routing"
    display_name = "Advanced Delegation 1.1.0"

    def summary(self) -> str:  # type: ignore[override]
        return "Routes by task class; prefers tested local models; free/grant-first with budget guardrails."

    def is_available(self) -> bool:  # type: ignore[override]
        return True  # pure config-driven; no network checks here

    def task_parameters(self, config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:  # type: ignore[override]
        settings = _settings_block(config)
        classes = settings.get("classes") if isinstance(settings, Mapping) else None
        enum = sorted(k for k in (classes or {}).keys() if isinstance(k, str))
        return {
            "task_class": {
                "type": "string",
                "description": "Task class routed by the advanced backend (e.g., code, analysis, retrieval, web, data).",
                **({"enum": enum} if enum else {}),
            }
        }

    def description(self, config: Mapping[str, Any]) -> str:  # type: ignore[override]
        return (
            "Advanced routing maps task_class to local/profile/cloud routes with explicit reasoning levels; "
            "routes may include fallback_providers and a profile target. Budgets and free/grant allowances are "
            "checked before cloud spend; exhausted routes are refused or downgraded per settings."
        )

    def _pick_route_for(self, task: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        settings = _settings_block(config)
        classes = settings.get("classes") if isinstance(settings, Mapping) else {}
        tclass = (task.get("task_class") or "").strip() if isinstance(task.get("task_class"), str) else None
        route = {}
        if isinstance(classes, Mapping) and tclass and isinstance(classes.get(tclass), Mapping):
            route = dict(classes[tclass])  # copy
        # Optional heuristic fallback: code-like goals to "code"; otherwise "analysis".
        if not route:
            goal = str(task.get("goal") or "").lower()
            if any(k in goal for k in ("fix", "refactor", "compile", "test", "build", "code", "pr ")):
                route = dict(classes.get("code") or {})
            else:
                route = dict(classes.get("analysis") or {})
        if not route:
            # No mapping: inherit default delegation route (empty dict = core inheritance rule)
            return {}
        return route

    def _enforce_budgets(self, route: dict[str, Any], settings: Mapping[str, Any]) -> tuple[bool, str | None]:
        # True when allowed; False with reason when exhausted.
        usage = _read_usage_cache(settings.get("usage_cache_path") if isinstance(settings, Mapping) else None)
        allowances = settings.get("allowances") if isinstance(settings, Mapping) else None
        key = _allowance_key(route)
        # explicit allowance wins when provided; else usage_cache remaining>0 is treated as allowed
        remaining = None
        if isinstance(allowances, Mapping) and isinstance(allowances.get(key), Mapping):
            remaining = allowances.get(key, {}).get("remaining")
        elif isinstance(usage, Mapping) and isinstance(usage.get(key), Mapping):
            remaining = usage.get(key, {}).get("remaining")
        if remaining is None:
            return True, None  # no opinion
        try:
            if int(remaining) > 0:
                return True, None
        except (ValueError, TypeError):
            return True, None
        return False, f"Budget exhausted for {key}"

    def route_task(self, task: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        settings = _settings_block(config)
        route = self._pick_route_for(task, config)
        if not route:
            return {}
        allowed, reason = self._enforce_budgets(route, settings)
        if allowed:
            return route
        # Try per-class fallback_providers first, else a global local/profile fallback
        fallbacks = []
        if isinstance(route, Mapping) and isinstance(route.get("fallback_providers"), list):
            fallbacks = [r for r in route["fallback_providers"] if isinstance(r, Mapping)]
        global_fallbacks = settings.get("global_fallbacks") if isinstance(settings, Mapping) else None
        if isinstance(global_fallbacks, list):
            fallbacks += [r for r in global_fallbacks if isinstance(r, Mapping)]
        for fb in fallbacks:
            ok, _ = self._enforce_budgets(fb, settings)
            if ok:
                return dict(fb)
        raise DelegationRouteError(reason or "Budget exhausted with no eligible fallback")


def register(ctx) -> None:
    ctx.register_delegation_provider(AdvancedRoutingProvider())
