"""Writer refusals remain closed while exposing only bounded diagnostic vocabulary."""
import argparse
import json
import os

import pytest

from hermes_cli.startup_hold_diagnostics import WriterRefusal, public_writer_diagnostic


@pytest.mark.parametrize('raw,detail', [
    (b'private invalid bytes', 'record_json_invalid'),
    (b'{}', 'record_shape_invalid'),
    (json.dumps(dict(schema=1, pid=42, create_time=None, state='exit_ready')).encode(),
     'record_birth_invalid'),
])
def test_record_refusal_is_body_free(raw, detail):
    from hermes_cli.startup_hold_writer_checkpoint import _record
    with pytest.raises(WriterRefusal) as caught:
        _record(raw)
    assert str(caught.value) == 'startup_hold_writers_busy'
    assert public_writer_diagnostic(caught.value) == dict(stage='writer_inventory', detail=detail)


@pytest.mark.parametrize('stage,detail', [
    ('admission', 'lock_unavailable'), ('writer_inventory', 'reference_unavailable'),
])
def test_cli_keeps_exit_and_error_without_private_exception_body(monkeypatch, capsys, stage, detail):
    from hermes_cli import gateway_kanban_window as cli
    error = WriterRefusal(stage, detail)
    error.args = ('private/path/record contents',)
    def fail(_args):
        raise error
    monkeypatch.setattr(cli, 'execute', fail)
    with pytest.raises(SystemExit) as stopped:
        cli.cmd_gateway_drain(argparse.Namespace(drain_action='reconcile'))
    assert stopped.value.code == 6
    assert json.loads(capsys.readouterr().out) == {
        'error': 'startup_hold_writers_busy', 'diagnostic': dict(stage=stage, detail=detail)}


@pytest.mark.parametrize('value', ['private/path', [], None])
def test_modified_diagnostic_values_are_not_exported(value):
    error = WriterRefusal('admission', 'lock_unavailable')
    error.detail = value
    assert public_writer_diagnostic(error) is None


def test_untrusted_exception_cannot_supply_diagnostics():
    class Other(WriterRefusal):
        pass
    assert public_writer_diagnostic(Other('admission', 'lock_unavailable')) is None
    assert public_writer_diagnostic(RuntimeError('private')) is None


@pytest.mark.platforms('posix')
def test_admission_lock_failure_still_refuses_and_closes_descriptor(tmp_path, monkeypatch):
    from hermes_cli import startup_hold
    from pm import filesystem
    monkeypatch.setattr(startup_hold, 'get_default_hermes_root', lambda: tmp_path)
    seen = []
    def unavailable(fd, **kwargs):
        seen.append(fd)
        return False
    monkeypatch.setattr(filesystem, 'lock_fd', unavailable)
    with pytest.raises(WriterRefusal) as caught:
        with startup_hold.admission_gate():
            pytest.fail('failed admission must not yield')
    assert public_writer_diagnostic(caught.value) == dict(stage='admission', detail='lock_unavailable')
    assert len(seen) == 1
    with pytest.raises(OSError):
        os.fstat(seen[0])


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('kind,detail', [
    ('held', 'reference_unavailable'), ('malformed', 'record_json_invalid'),
    ('hardlink', 'unsafe_reference'), ('oversize', 'inventory_limit'),
])
def test_capture_refusal_retains_records_and_releases_owned_descriptors(tmp_path, monkeypatch, kind, detail):
    from hermes_cli import startup_hold_writer_checkpoint as writers
    from pm.lifetime_handles import open_hold
    monkeypatch.setattr(writers, 'get_default_hermes_root', lambda: tmp_path)
    directory = tmp_path / '.startup-writers'
    directory.mkdir(mode=0o700)
    path = directory / 'record.hold'
    path.write_text(json.dumps(dict(schema=1, pid=42, create_time=None, state='unsettled')))
    held = None
    if kind == 'held':
        held = open_hold(path)
        assert held is not None
    elif kind == 'malformed':
        path.write_bytes(b'private malformed bytes')
    elif kind == 'hardlink':
        os.link(path, tmp_path / 'link')
    else:
        path.write_bytes(b' ' * 1025)
    before = path.read_bytes()
    try:
        with pytest.raises(WriterRefusal) as caught:
            with writers.capture_writers():
                pytest.fail('refusal must precede admission')
        assert public_writer_diagnostic(caught.value) == dict(stage='writer_inventory', detail=detail)
        assert path.read_bytes() == before
    finally:
        if held is not None:
            os.close(held)
    fd = open_hold(path)
    assert fd is not None
    os.close(fd)
