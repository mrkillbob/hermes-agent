"""String-only model identifier matching and version normalization."""


def _model_id_matches(candidate_id: str, lookup_model: str) -> bool:
    """Exact match, or ``publisher/slug`` (LM Studio native ids) whose slug equals the configured name."""
    return candidate_id == lookup_model or ("/" in candidate_id and candidate_id.rsplit("/", 1)[1] == lookup_model)


def _normalize_model_version(model: str) -> str:
    """Dots -> dashes so Nous ids (claude-opus-4-6) compare with OpenRouter's (claude-opus-4.6)."""
    return model.replace(".", "-")
