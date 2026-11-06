from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from plugins.delegation.advanced_routing import AdvancedRoutingProvider


def run() -> dict[str, Any]:
    cfg = {
        "backend": "advanced-routing",
        "backends": {
            "advanced-routing": {
                "classes": {
                    "analysis": {"provider": "openrouter", "model": "thinkingmachines/inkling-small:free", "reasoning_effort": "medium",
                                   "fallback_providers": [{"provider": "llamacpp", "model": "Devstral-Small-2-24B-Instruct-2512-Q4_K_M"}]},
                    "code": {"provider": "llamacpp", "model": "Devstral-Small-2-24B-Instruct-2512-Q4_K_M", "reasoning_effort": "none"},
                },
                "allowances": {"openrouter:thinkingmachines/inkling-small:free": {"remaining": 1}},
            }
        }
    }
    p = AdvancedRoutingProvider()
    t0 = time.perf_counter()
    r1 = p.route_task({"goal": "Summarize doc", "task_class": "analysis"}, cfg)
    r2 = p.route_task({"goal": "Fix function", "task_class": "code"}, cfg)
    dt = time.perf_counter() - t0
    return {"latency_ms": int(dt * 1000), "routes": [r1, r2]}


if __name__ == "__main__":
    out = run()
    Path("artifacts").mkdir(exist_ok=True)
    with open("artifacts/advanced_routing_bench.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out))
