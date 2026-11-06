from __future__ import annotations

import json
from typing import Any

# Non-secret dry-run mock that documents eligible free/grant routes and reads no credentials.
# Sources (repo-local):
#  - plugin-catalog/quota.yaml (community quota widget)
#  - plugin-catalog/model-usage-status.yaml (allowance windows widget)
#  - docs/advanced-delegation/ROUTES.md (policy)
# External terms should be reviewed in provider docs during release; no network calls here.


def run() -> dict[str, Any]:
    return {
        "verified_sources": [
            "plugin-catalog/quota.yaml",
            "plugin-catalog/model-usage-status.yaml",
            "docs/advanced-delegation/ROUTES.md",
        ],
        "eligible_models": [
            {"provider": "openrouter", "model": "thinkingmachines/inkling-small:free", "limit": "provider free tier (documented in provider terms)", "notes": "use only when allowance>0"}
        ],
        "network_calls": False,
        "secrets_used": False,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
