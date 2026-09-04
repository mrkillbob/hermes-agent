from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_revenue_lab.compliance import (
    REQUIRED_GLOBAL_PROHIBITIONS,
    ComplianceRegistry,
    DecisionStatus,
    RegistryError,
    load_registry,
)


def _platform_policy(**overrides: object) -> dict[str, object]:
    policy: dict[str, object] = {
        "platform": "example_public_api",
        "automation_allowed": True,
        "ai_content_policy": {"status": "BLOCK_AND_REVIEW", "requirements": []},
        "scraping_policy": {"status": "BLOCK", "requirements": []},
        "api_requirements": {"status": "ALLOW", "requirements": ["official_api_key"]},
        "outreach_policy": {"status": "BLOCK", "requirements": []},
        "rate_limits": {
            "status": "DECLARED",
            "max_requests": 60,
            "period_seconds": 60,
        },
        "prohibited_behaviors": [],
        "source": "https://example.test/official-api-policy",
        "last_verified": "2026-08-21",
    }
    policy.update(overrides)
    return policy


def _registry_payload(*platforms: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "hrl.compliance_registry.v1",
        "global_prohibited_behaviors": sorted(REQUIRED_GLOBAL_PROHIBITIONS),
        "platforms": list(platforms),
    }


def test_unknown_or_unclear_policy_fails_closed() -> None:
    registry = ComplianceRegistry.from_mapping(_registry_payload())
    unknown = registry.evaluate(platform="not_registered", action="use_api")
    assert unknown.status is DecisionStatus.BLOCK_AND_REVIEW
    assert unknown.reason == "platform_not_registered"

    unclear_registry = ComplianceRegistry.from_mapping(
        _registry_payload(
            _platform_policy(
                api_requirements={"status": "BLOCK_AND_REVIEW", "requirements": []}
            )
        )
    )
    unclear = unclear_registry.evaluate(platform="example_public_api", action="use_api")
    assert unclear.status is DecisionStatus.BLOCK_AND_REVIEW
    assert unclear.reason == "api_requirements_unclear"


@pytest.mark.parametrize("action", sorted(REQUIRED_GLOBAL_PROHIBITIONS))
def test_global_prohibitions_cannot_be_overridden_by_a_platform(action: str) -> None:
    registry = ComplianceRegistry.from_mapping(_registry_payload(_platform_policy()))

    decision = registry.evaluate(platform="example_public_api", action=action)

    assert decision.status is DecisionStatus.BLOCK
    assert decision.reason == "globally_prohibited_behavior"


def test_api_collection_requires_explicit_policy_and_rate_limit_evidence() -> None:
    registry = ComplianceRegistry.from_mapping(_registry_payload(_platform_policy()))

    allowed = registry.evaluate(platform="example_public_api", action="use_api")

    assert allowed.status is DecisionStatus.ALLOW
    assert allowed.reason == "explicit_policy_allow"
    assert allowed.policy_source == "https://example.test/official-api-policy"
    assert allowed.last_verified == "2026-08-21"
    assert allowed.rate_limits == {"max_requests": 60, "period_seconds": 60}
    assert len(allowed.registry_sha256) == 64

    missing_limits = ComplianceRegistry.from_mapping(
        _registry_payload(_platform_policy(rate_limits={"status": "UNKNOWN"}))
    ).evaluate(platform="example_public_api", action="use_api")
    assert missing_limits.status is DecisionStatus.BLOCK_AND_REVIEW
    assert missing_limits.reason == "rate_limits_unclear"


def test_registry_rejects_incomplete_global_prohibitions_and_duplicate_platforms() -> (
    None
):
    missing_global = _registry_payload()
    missing_global["global_prohibited_behaviors"] = ["mass_unsolicited_spam"]
    with pytest.raises(RegistryError, match="missing_global_prohibitions"):
        ComplianceRegistry.from_mapping(missing_global)

    duplicate = _platform_policy()
    with pytest.raises(RegistryError, match="duplicate_platform"):
        ComplianceRegistry.from_mapping(_registry_payload(duplicate, duplicate))


def test_repository_registry_is_machine_readable_and_fail_closed() -> None:
    path = Path(__file__).parents[1] / "config" / "compliance_registry.json"
    parsed = json.loads(path.read_text(encoding="utf-8"))
    registry = load_registry(path)

    assert parsed["schema_version"] == "hrl.compliance_registry.v1"
    assert set(parsed["global_prohibited_behaviors"]) == REQUIRED_GLOBAL_PROHIBITIONS
    assert registry.evaluate(platform="etsy", action="publish_ai_content").status is (
        DecisionStatus.BLOCK_AND_REVIEW
    )


def test_compliance_cli_emits_a_machine_readable_fail_closed_receipt(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(_registry_payload()), encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts" / "check_compliance.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--registry",
            str(registry_path),
            "--platform",
            "etsy",
            "--action",
            "publish_ai_content",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 3
    receipt = json.loads(completed.stdout)
    assert receipt["status"] == "BLOCK_AND_REVIEW"
    assert receipt["reason"] == "platform_not_registered"
    assert receipt["platform"] == "etsy"
    assert receipt["action"] == "publish_ai_content"
