"""Adopted growth must use verified serving bytes, never consumer backend policy."""

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('state_case', ['verified', 'foreign', 'unverified', 'unregistered'])
def test_managed_engine_requires_identity_and_registered_serving_binary(tmp_path, monkeypatch, state_case):
    from hermes_cli.local_runtime import binaries, endpoint, recovery
    import pm

    binary = tmp_path / 'owner-vulkan' / 'llama-server'
    state = {'base_url': 'http://127.0.0.1:18434/v1'}
    monkeypatch.setattr(recovery, 'read_state', lambda: state)
    monkeypatch.setattr(recovery, 'is_modern', lambda state: True)
    monkeypatch.setattr(recovery, 'recorded_process', lambda state: None if state_case == 'unverified'
                        else SimpleNamespace(exe=lambda: str(binary)))
    def installed(name, *, allow_outdated):
        assert allow_outdated
        if name == binaries.BACKEND_PACKAGES['vulkan'] and state_case != 'unregistered':
            return SimpleNamespace(version='fixture', binary=binary)
        return None
    monkeypatch.setattr(pm, 'installed_package', installed)
    monkeypatch.setattr(binaries, 'adopt_legacy_engine', lambda *args: pytest.fail('read must not adopt/install'))
    base = 'http://127.0.0.1:9999/v1' if state_case == 'foreign' else state['base_url']
    actual = endpoint.managed_engine(base)
    assert actual == (binaries.Engine('vulkan', 'bfixture', binary) if state_case == 'verified' else None)


def test_scoped_serving_engine_prices_amd_device_without_consumer_backend(tmp_path, monkeypatch):
    from hermes_cli import config
    from hermes_cli.local_runtime import binaries, bootstrap, devices, hardware

    gib = 1 << 30
    owner = binaries.Engine('vulkan', 'fixture', tmp_path / 'owner' / 'llama-server')
    consumer = binaries.Engine('cpu', 'fixture', tmp_path / 'consumer' / 'llama-server')
    monkeypatch.setattr(bootstrap, 'serving_engine', lambda: None)
    monkeypatch.setattr(config, 'load_config_readonly', lambda: {'local_runtime': {'backend': 'cpu'}})
    monkeypatch.setattr(binaries, 'installed_engine', lambda backend: consumer)
    monkeypatch.setattr(hardware, '_ram_bytes', lambda: (64 * gib, 32 * gib))
    monkeypatch.setattr(hardware, '_nvidia_vram', lambda: None)
    monkeypatch.setattr(hardware, '_cuda_driver_pool', lambda: None)
    monkeypatch.setattr(hardware, '_engine_device_pool', lambda: None)
    monkeypatch.setattr(hardware, '_accelerator_cache', {})
    def probe(directory, backend):
        assert directory == owner.binary.parent and backend == 'vulkan'
        return [{'type': devices.GGML_DEVICE_GPU, 'total': 24 * gib, 'free': 12 * gib,
                 'description': 'fixture AMD device'}]
    monkeypatch.setattr(devices, 'probe_devices', probe)
    with hardware.engine_scope(owner):
        budget = hardware.probe_budget(planning=True)
        assert budget.total_device_bytes == 24 * gib and not budget.uma
    assert hardware._configured_engine() == consumer


def test_scoped_engine_does_not_reuse_other_engine_pool_cache(tmp_path, monkeypatch):
    from hermes_cli.local_runtime import binaries, hardware

    original_cache = (0.0, (10, None))
    monkeypatch.setattr(hardware, '_pool_probe_cache', original_cache)
    monkeypatch.setattr(hardware, '_cuda_driver_pool', lambda: None)
    monkeypatch.setattr(hardware, '_engine_device_pool', lambda: (20, None))
    engine = binaries.Engine('vulkan', 'fixture', tmp_path / 'owner-engine')
    with pytest.raises(RuntimeError):
        with hardware.engine_scope(engine):
            assert hardware._device_pool_view() == (20, None)
            raise RuntimeError('leave scope')
    assert hardware._pool_probe_cache == original_cache
    assert hardware._device_pool_view() == (10, None)
