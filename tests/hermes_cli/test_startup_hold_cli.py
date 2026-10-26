"""The operator's hold-only command never selects a task or exposes prior marker bytes."""
from pathlib import Path
import os
import json

import pytest

from tests.hermes_cli.test_gateway_kanban_window import parse


def test_hold_status_restore_without_card(tmp_path, monkeypatch):
    from hermes_cli.gateway_kanban_window import execute
    from gateway import drain_startup
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(drain_startup, '_refuse_legacy_writers', lambda *args: None)
    monkeypatch.setattr(drain_startup, '_checkout_root', lambda: Path(os.environ['HERMES_HOME']))
    (tmp_path / 'ESTOP').write_text('retained pause')
    result = execute(parse('hold'))
    assert result['mode'] == 'startup_hold' and result['armed'] is False
    assert 'previous_drain' not in json.dumps(result)
    assert execute(parse('status')) == result
    assert execute(parse('restore', '--hold-id', result['hold_id'])) == {'restored': True}
    assert not (tmp_path / '.drain_request.json').exists()
    assert not (tmp_path / 'kanban.db').exists()


@pytest.mark.parametrize('args', [('restore',), ('restore', '--hold-id', 'a', '--window-id', 'b'), ('arm', '--hold-id', 'a')])
def test_hold_identity_is_not_a_window_grant(args):
    with pytest.raises(SystemExit):
        parse(*args)
