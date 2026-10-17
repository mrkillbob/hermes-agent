"""The terminal setup submits and follows jobs on the durable gateway, never in the CLI."""
import pytest


@pytest.mark.parametrize('outcome', ['done', 'error'])
def test_setup_uses_gateway_jobs_without_in_process_runtime(monkeypatch, capsys, outcome):
    from hermes_cli import model_setup_flows_local as flow, main_provider_setup, setup
    from hermes_cli.web_routers import local_models as lm

    monkeypatch.setattr(lm, 'local_models_hardware', lambda: {'vram_label': '8 GB'})
    monkeypatch.setattr(lm, 'local_models_status', lambda: {
        'runtime_installed': True, 'tag': 'b1', 'runtime_backend': 'cpu', 'models': []})
    monkeypatch.setattr(lm, 'local_models_catalog', lambda: {'models': [{
        'id': 'tiny', 'display_name': 'Tiny', 'fits': True, 'downloaded': False,
        'size_label': '1 GB', 'recommended': True}]})
    monkeypatch.setattr(main_provider_setup, '_prompt_provider_choice', lambda *a, **k: 0)
    monkeypatch.setattr(setup, 'prompt_yes_no', lambda *a, **k: True)
    monkeypatch.setattr(lm, 'local_models_quickstart', lambda *a: pytest.fail('CLI invoked server handler'))
    calls = []

    class Gateway:
        def get(self, path):
            calls.append(('GET', path))
            if path == 'status':
                return {'control_available': True, 'server_running': False}
            return {'status': outcome, 'detail': 'Tiny is ready', 'error': 'disk full'}

        def post(self, path, body):
            calls.append(('POST', path, body))
            return {'job_id': 'gateway-job'}

    monkeypatch.setattr(flow, '_connect_local_models', lambda: Gateway(), raising=False)
    flow._model_flow_local({})
    assert ('POST', 'quickstart', {'model_id': 'tiny'}) in calls
    assert ('GET', 'jobs/gateway-job') in calls
    output = capsys.readouterr().out
    assert ('Default model set to: Tiny' in output) == (outcome == 'done')
    if outcome == 'error':
        assert 'disk full' in output


@pytest.mark.parametrize('control,server_running', [(False, False), (False, True)])
def test_no_control_refuses_new_download_before_submission(monkeypatch, capsys, control, server_running):
    from hermes_cli import model_setup_flows_local as flow, main_provider_setup
    from hermes_cli.web_routers import local_models as lm

    monkeypatch.setattr(lm, 'local_models_hardware', dict)
    monkeypatch.setattr(lm, 'local_models_status', lambda: {
        'runtime_installed': True, 'tag': 'b1', 'runtime_backend': 'cpu', 'models': []})
    monkeypatch.setattr(lm, 'local_models_catalog', lambda: {'models': [{
        'id': 'tiny', 'display_name': 'Tiny', 'fits': True, 'size_label': '1 GB'}]})
    monkeypatch.setattr(main_provider_setup, '_prompt_provider_choice', lambda *a, **k: 0)

    class Gateway:
        def get(self, path):
            assert path == 'status'
            return {'control_available': control, 'server_running': server_running}

        def post(self, *args):
            pytest.fail('download submitted without durable control authority')

    monkeypatch.setattr(flow, '_connect_local_models', lambda: Gateway())
    flow._model_flow_local({})
    assert 'no downloads started' in capsys.readouterr().out


def test_named_setup_never_ensures_or_mints_root_authority(monkeypatch, tmp_path):
    from hermes_cli import model_setup_flows_local as flow, gateway_runtime
    from hermes_cli.local_model_client import LocalModelClientError
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home = tmp_path / 'root' / 'profiles' / 'named'
    home.mkdir(parents=True)
    token = set_hermes_home_override(home)
    monkeypatch.setattr(gateway_runtime, 'discover_gateway_endpoint', lambda h: gateway_runtime.GatewayDiscovery('absent'))
    monkeypatch.setattr(gateway_runtime, 'ensure_gateway_runtime', lambda *a, **k: pytest.fail('named setup started a root'))
    try:
        with pytest.raises(LocalModelClientError, match='default profile first'):
            flow._connect_local_models()
    finally:
        reset_hermes_home_override(token)


def test_absent_root_gateway_requires_explicit_background_choice(monkeypatch, tmp_path):
    from hermes_cli import model_setup_flows_local as flow, gateway_runtime, setup
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home = tmp_path / 'root'
    home.mkdir()
    token = set_hermes_home_override(home)
    monkeypatch.setattr(gateway_runtime, 'discover_gateway_endpoint', lambda h: gateway_runtime.GatewayDiscovery('absent'))
    monkeypatch.setattr(gateway_runtime, 'ensure_gateway_runtime', lambda *a, **k: pytest.fail('declined gateway started'))
    questions = []
    monkeypatch.setattr(setup, 'prompt_yes_no', lambda question, default: questions.append((question, default)) or False)
    try:
        assert flow._connect_local_models() is None
    finally:
        reset_hermes_home_override(token)
    assert questions and questions[0][1] is False
    assert 'background' in questions[0][0]
