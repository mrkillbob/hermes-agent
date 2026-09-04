"""Evidence-complete HRL-6 opportunity assessment and ranking."""

from .engine import build_assessment, rank_assessments
from .types import (
    DIMENSIONS,
    OPPORTUNITY_FIELDS,
    RANKING_FACTORS,
    DimensionScore,
    EvidenceItem,
    FactorRating,
    ObservedField,
    OpportunityAssessment,
    OpportunityCandidate,
)

__all__ = [
    "DIMENSIONS",
    "OPPORTUNITY_FIELDS",
    "RANKING_FACTORS",
    "DimensionScore",
    "EvidenceItem",
    "FactorRating",
    "ObservedField",
    "OpportunityAssessment",
    "OpportunityCandidate",
    "build_assessment",
    "rank_assessments",
]
