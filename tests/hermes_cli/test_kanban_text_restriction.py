"""The text-only restriction is part of the exact one-worker claim budget."""
from types import SimpleNamespace

import pytest

from hermes_cli.kanban_execution_budget import dispatch_budget, validate_budget


@pytest.mark.parametrize('marker,requests,allowed', [(True, 1, True), (True, 2, False),
    (False, 1, False), (1, 1, False), ('true', 1, False), (None, 1, False)])
def test_restriction_is_explicit_and_single_request(marker, requests, allowed):
    value = dict(wall_seconds=60, output_bytes=8192, requests=requests, local_text_only=marker)
    if allowed:
        result = validate_budget(value)
        assert result == value and result is not value
    else:
        with pytest.raises(ValueError):
            validate_budget(value)


def test_dispatch_carries_restriction_and_rejects_unbounded_flag():
    args = SimpleNamespace(wall_seconds=60, output_bytes=8192, requests=1, max=1, local_text_only=True)
    assert dispatch_budget(args, ('t_public',))['local_text_only'] is True
    args.wall_seconds = args.output_bytes = args.requests = None
    with pytest.raises(ValueError):
        dispatch_budget(args, ('t_public',))
    args.local_text_only = False
    assert dispatch_budget(args, None) is None
