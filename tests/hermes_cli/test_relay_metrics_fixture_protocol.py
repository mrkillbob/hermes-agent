"""The metrics double implements the current native lifecycle without policy bypass."""
import pytest

from agent import relay_runtime
from tests.hermes_cli.test_relay_shared_metrics_runtime import _Relay


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    relay_runtime._reset_for_tests()
    monkeypatch.delenv(relay_runtime.RELAY_PLUGINS_CONFIG_ENV, raising=False)
    yield
    relay_runtime._reset_for_tests()


def test_empty_activation_initializes_and_flushes_before_close(caplog):
    relay = _Relay()
    host = relay_runtime.RelayRuntime(relay=relay, profile_key='fixture')
    try:
        assert relay.plugin_events == [('initialize', {}, None)]
        assert not host.managed_execution_enabled()
        assert host._plugin_configuration_state is relay_runtime._RelayPluginConfigurationState.DISABLED
        assert relay.plugin_activations[0].is_active is True
    finally:
        host.shutdown()
    assert relay.plugin_events[-2:] == [('flush',), ('close',)]
    assert relay.plugin_activations[0].is_active is False
    assert 'initialization failed' not in caplog.text


def test_last_profile_releases_the_shared_activation():
    relay = _Relay()
    first = relay_runtime.RelayRuntime(relay=relay, profile_key='first')
    second = relay_runtime.RelayRuntime(relay=relay, profile_key='second')
    first.shutdown()
    assert relay.plugin_events == [('initialize', {}, None)]
    second.shutdown()
    assert relay.plugin_events == [('initialize', {}, None), ('flush',), ('close',)]
