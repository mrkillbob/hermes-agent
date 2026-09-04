"""Deterministic HRL-7 scout eligibility rules."""

from __future__ import annotations

from .types import ScoutCandidate, ScoutVerdict


_BUSINESS_FACTS = {
    "broken_conversion_path",
    "broken_public_link",
    "outdated_public_information",
    "mobile_usability_failure",
    "measured_performance_failure",
    "public_reputation_metric",
    "competitor_disadvantage",
    "missing_public_functionality",
}
_ALERT_EVENTS = {
    "public_rfp",
    "permit",
    "grant",
    "new_business_opening",
    "pricing_change",
    "regulatory_update",
    "vendor_opportunity",
    "niche_inventory",
    "competitor_change",
}
_DIGITAL_TYPES = {
    "calculator",
    "spreadsheet",
    "business_template",
    "planning_tool",
    "niche_reference",
    "specialized_utility",
}


def evaluate_candidate(candidate: ScoutCandidate) -> ScoutVerdict:
    codes = {item.fact_code for item in candidate.evidence}
    reasons: list[str] = []
    if candidate.scout_kind == "business_problem":
        if not codes & _BUSINESS_FACTS:
            reasons.append("objective_problem_evidence_missing")
    elif candidate.scout_kind == "data_opportunity":
        requirements = {
            "fragmented_sources": "fragmentation_evidence_missing",
            "repeated_updates": "repeat_update_evidence_missing",
            "economically_useful": "economic_value_evidence_missing",
            "historical_dataset_value": "historical_value_missing",
        }
        reasons.extend(reason for code, reason in requirements.items() if code not in codes)
    elif candidate.scout_kind == "alert_opportunity":
        if not any(item.fact_code in _ALERT_EVENTS for item in candidate.evidence):
            reasons.append("qualifying_public_event_missing")
        if "notification_monetary_value" not in codes:
            reasons.append("notification_value_missing")
        if not any(
            item.source_class in {"authoritative_public", "public_api"}
            for item in candidate.evidence
        ):
            reasons.append("authoritative_source_missing")
    else:
        product_types = {
            item.fact_value for item in candidate.evidence if item.fact_code == "product_type"
        }
        if "generic_ai_art" in product_types:
            return ScoutVerdict(candidate.candidate_id, False, ("generic_ai_art_rejected",))
        if not product_types & _DIGITAL_TYPES:
            reasons.append("narrow_utility_type_missing")
        if "demonstrable_demand" not in codes:
            reasons.append("demand_evidence_missing")
    return ScoutVerdict(candidate.candidate_id, not reasons, tuple(reasons))
