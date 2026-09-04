"""Conservative busy and idle classification for HRL-0."""

from collections.abc import Mapping, Sequence


def classify_resource_state(samples: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Classify only from complete evidence; missing evidence never means idle."""

    if any(int(sample.get("luna_count") or 0) > 0 for sample in samples):
        return {
            "classification": "observed_busy",
            "reasons": ["luna_active"],
            "idle_baseline": {"status": "unavailable", "value": None},
        }
    if any(int(sample.get("loaded_models") or 0) > 0 for sample in samples):
        return {
            "classification": "observed_busy",
            "reasons": ["ollama_model_loaded"],
            "idle_baseline": {"status": "unavailable", "value": None},
        }
    required = ("load_1m", "memory_free_percent", "revenue_lab_workers")
    if len(samples) < 3 or any(
        sample.get(key) is None for sample in samples for key in required
    ):
        return {
            "classification": "not_observed",
            "reasons": ["quiet_window_unavailable"],
            "idle_baseline": {"status": "unavailable", "value": None},
        }
    quiet = all(
        float(sample["load_1m"]) < 3.0
        and float(sample["memory_free_percent"]) >= 35.0
        and int(sample["revenue_lab_workers"]) == 0
        for sample in samples
    )
    return {
        "classification": "observed_idle" if quiet else "observed_busy",
        "reasons": [] if quiet else ["resource_threshold"],
        "idle_baseline": {
            "status": "available" if quiet else "unavailable",
            "value": list(samples) if quiet else None,
        },
    }
