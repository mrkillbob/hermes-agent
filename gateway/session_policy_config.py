"""Copy YAML configuration into the JSON-only frozen session policy."""
from datetime import date
import json


def _normalize_yaml_dates(value):
    """Normalize YAML dates wherever JSON can represent their enclosing shape."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        normalized = {}
        converted_keys = set()
        for key, item in value.items():
            normalized_key = key.isoformat() if isinstance(key, date) else key
            converted = normalized_key != key
            if normalized_key in normalized:
                if converted or normalized_key in converted_keys:
                    raise ValueError(
                        f"YAML date key {normalized_key!r} collides with another configuration key"
                    )
            normalized[normalized_key] = _normalize_yaml_dates(item)
            if converted:
                converted_keys.add(normalized_key)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_yaml_dates(item) for item in value]
    return value


def _yaml_date(value):
    # Safe YAML loaders construct date/datetime for unquoted ISO values. Preserve
    # their calendar/offset information in the durable policy, without stringifying
    # arbitrary unsupported configuration objects.
    if isinstance(value, date):
        return value.isoformat()
    return json.JSONEncoder().default(value)


def copy_config_for_policy(config):
    return json.loads(json.dumps(_normalize_yaml_dates(config), default=_yaml_date))
