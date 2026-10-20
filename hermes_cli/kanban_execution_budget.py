"""Closed, opt-in bounds carried by one exact native-owner claim."""

_LIMITS = {'wall_seconds': 120, 'output_bytes': 65536, 'requests': 4}


def validate_budget(value):
    """Return a fresh normalized mapping; booleans/floats and partial budgets fail closed."""
    if (not isinstance(value, dict) or set(value) != set(_LIMITS)
            or any(type(value[key]) is not int or not 0 < value[key] <= maximum
                   for key, maximum in _LIMITS.items())):
        raise ValueError('execution_budget requires positive integer wall_seconds<=120, output_bytes<=65536, requests<=4')
    return dict(value)


def dispatch_budget(args, task_ids):
    """CLI budgets are all-or-none and require explicit one-card/one-spawn selection."""
    values = {key: getattr(args, key, None) for key in _LIMITS}
    if all(value is None for value in values.values()):
        return None
    budget = validate_budget(values)
    if task_ids is None or len(task_ids) != 1 or getattr(args, 'max', None) != 1:
        raise ValueError('execution_budget requires one --task-id and --max 1')
    return budget
