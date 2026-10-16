"""Copy YAML configuration into the JSON-only frozen session policy."""
from datetime import date
import json


def _yaml_date(value):
    # Safe YAML loaders construct date/datetime for unquoted ISO values. Preserve
    # their calendar/offset information in the durable policy, without stringifying
    # arbitrary unsupported configuration objects.
    if isinstance(value, date):
        return value.isoformat()
    return json.JSONEncoder().default(value)


def copy_config_for_policy(config):
    return json.loads(json.dumps(config, default=_yaml_date))
