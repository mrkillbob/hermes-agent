"""Full controller custody across a native device-number remap."""
import os

import pytest

from tests.hermes_cli.test_startup_hold_reboot import prepare, _AFTER

pytest_plugins = ['tests.hermes_cli.test_startup_hold_reboot']
pytestmark = pytest.mark.platforms('macos')


def test_full_checkout_custody_survives_remapped_device_with_same_volume(environment, monkeypatch):
    from hermes_cli import startup_hold_reboot as reboot
    from gateway import drain_startup
    home, _ = environment
    ledger_before = (home / 'spawn-ledger.json').read_bytes()
    prepare(environment)
    monkeypatch.setattr(reboot, '_native_identity', lambda: dict(_AFTER))
    original_stat, original_lstat, original_fstat = os.stat, os.lstat, os.fstat
    class Remapped:
        def __init__(self, value):
            self.value = value
        def __getattr__(self, name):
            return getattr(self.value, name) + (1000 if name == 'st_dev' else 0)
        def __getitem__(self, index):
            return self.value[index] + (1000 if index == 2 else 0)
    with monkeypatch.context() as current:
        current.setattr(os, 'stat', lambda *args, **kwargs: Remapped(original_stat(*args, **kwargs)))
        current.setattr(os, 'lstat', lambda *args, **kwargs: Remapped(original_lstat(*args, **kwargs)))
        current.setattr(os, 'fstat', lambda *args, **kwargs: Remapped(original_fstat(*args, **kwargs)))
        # Retain the REAL checkout/runtime gate; no custody mock or state deletion.
        value = drain_startup.begin()
        drain_startup.restore(hold_id=value['hold_id'])
    assert (home / 'spawn-ledger.json').read_bytes() == ledger_before
    assert not (home / '.startup-hold.json').exists()
    assert not (home / '.drain_request.json').exists()
