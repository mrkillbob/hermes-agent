"""CLI setup reaches the real native HTTP owner with a temporary home and inference disabled."""
import pytest

from tests.gateway.test_native_http_auth import daemon


@pytest.mark.platforms('linux', 'macos', 'windows')
def test_client_can_preflight_real_durable_gateway_without_enabling_inference(tmp_path):
    from hermes_cli.local_model_client import LocalModelClient, LocalModelClientError
    from hermes_cli.gateway_runtime import discover_gateway_endpoint

    with daemon(tmp_path) as (home, _descriptor):
        config = home / 'config.yaml'
        before = config.read_bytes()
        observed = discover_gateway_endpoint(home)
        assert observed.state == 'ready'
        client = LocalModelClient(home, observed.endpoint)
        status = client.get('status')
        assert status['control_available'] is True
        assert status['enabled'] is False
        assert status['server_running'] is False
        # A received refusal is not mistaken for a submitted job or a changed default.
        with pytest.raises(LocalModelClientError, match='not downloaded'):
            client.post('activate', {'model_id': 'unavailable-test-model'})
        assert config.read_bytes() == before
