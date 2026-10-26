"""Closed, opt-in bounds carried by one exact native-owner claim."""

_LIMITS = {'wall_seconds': 120, 'output_bytes': 65536, 'requests': 4}


def validate_budget(value):
    """Return a fresh normalized mapping; booleans/floats and partial budgets fail closed."""
    if (not isinstance(value, dict) or not set(_LIMITS) <= set(value) <= set(_LIMITS) | {'local_text_only'}
            or any(type(value[key]) is not int or not 0 < value[key] <= maximum
                   for key, maximum in _LIMITS.items())):
        raise ValueError('execution_budget requires positive integer wall_seconds<=120, output_bytes<=65536, requests<=4')
    if 'local_text_only' in value and (value['local_text_only'] is not True or value['requests'] != 1):
        raise ValueError('local_text_only requires true and exactly one request')
    return dict(value)


def dispatch_budget(args, task_ids):
    """CLI budgets are all-or-none and require explicit one-card/one-spawn selection."""
    values = {key: getattr(args, key, None) for key in _LIMITS}
    restricted = getattr(args, 'local_text_only', False)
    if type(restricted) is not bool:
        raise ValueError('local_text_only requires a boolean')
    if all(value is None for value in values.values()) and not restricted:
        return None
    if restricted:
        values['local_text_only'] = True
    budget = validate_budget(values)
    if task_ids is None or len(task_ids) != 1 or getattr(args, 'max', None) != 1:
        raise ValueError('execution_budget requires one --task-id and --max 1')
    return budget
