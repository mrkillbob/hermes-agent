"""Minimal current native plugin lifecycle for the shared-metrics Relay double.

The empty activation is still inspected by real production policy. No consumer,
configuration decision or native initializer is patched out.
"""
from types import SimpleNamespace
from typing import Any


class _Request:
    def __init__(self, headers: dict[str, Any], content: dict[str, Any]) -> None:
        self.headers = headers
        self.content = content


def install_native_plugin_protocol(relay):
    relay.plugin_events = []
    relay.plugin_activations = []

    class Activation:
        def __init__(self):
            self.is_active = True
            self.report = {'config': {'diagnostics': []}, 'config_paths': [],
                           'dynamic_plugins': [], 'resolved_config': {'components': []}}

        async def close(self):
            if self.is_active:
                self.is_active = False
                relay.plugin_events.append(('close',))

    async def initialize(config, additional_plugins_toml=None):
        relay.plugin_events.append(('initialize', config, additional_plugins_toml))
        activation = Activation()
        relay.plugin_activations.append(activation)
        return activation

    async def flush_async():
        relay.plugin_events.append(('flush',))
        relay._flush()

    relay.plugin = SimpleNamespace(initialize=initialize)
    relay.subscribers.flush_async = flush_async
