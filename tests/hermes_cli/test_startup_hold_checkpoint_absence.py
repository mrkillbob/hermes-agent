"""No prospective checkpoint keeps the ordinary cross-platform writer path."""
from contextlib import contextmanager


def test_absent_checkpoint_uses_ordinary_exclusion(tmp_path, monkeypatch):
    from hermes_cli import startup_hold, startup_hold_writer_checkpoint as checkpoint
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    covered = {(123, 12.5)}
    entered = []

    @contextmanager
    def ordinary():
        entered.append(True)
        yield covered

    monkeypatch.setattr(startup_hold, 'writer_exclusion', ordinary)
    with checkpoint.reconciliation_writers() as (actual, snapshot):
        assert actual is covered and snapshot is None
    assert entered == [True]
