"""Fail-closed platform policy registry for Revenue Lab actions."""

from .registry import (
    REQUIRED_GLOBAL_PROHIBITIONS,
    ComplianceDecision,
    ComplianceRegistry,
    DecisionStatus,
    RegistryError,
    load_registry,
)

__all__ = [
    "REQUIRED_GLOBAL_PROHIBITIONS",
    "ComplianceDecision",
    "ComplianceRegistry",
    "DecisionStatus",
    "RegistryError",
    "load_registry",
]
