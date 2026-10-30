"""``hermes model`` → Local models runs the desktop's Local Models setup in the terminal."""

from __future__ import annotations

import pytest


def _picker_keys(config: dict) -> list[str]:
    from hermes_cli.main_provider_setup import _build_provider_picker_rows

    return [key for key, _, _ in _build_provider_picker_rows(config, "", {}, {})[0]]


def test_local_models_row_follows_the_engine_pin_and_exclusions():
    from hermes_cli.local_runtime import binaries

    assert ("llamacpp" in _picker_keys({})) == (binaries.unavailable_reason("cpu") is None)
    assert "llamacpp" not in _picker_keys({"model_catalog": {"excluded_providers": ["llama.cpp"]}})


def test_interrupt_pauses_gateway_job_and_preserves_bytes(monkeypatch, capsys):
    from hermes_cli import model_setup_flows_local as flow
    from hermes_cli.web_routers import local_models as lm

    calls = []

    class Gateway:
        def get(self, path):
            calls.append(('get', path))
            if len(calls) == 1:
                raise KeyboardInterrupt
            return {'status': 'paused'}

        def post(self, path, body):
            calls.append(('post', path, body))
            return {'paused': True}

    assert flow._follow_job(lm, Gateway(), 'job-1') == 'paused'
    assert ('post', 'download/pause', {'job_id': 'job-1'}) in calls
    assert 'finished bytes are kept' in capsys.readouterr().out


def test_disconnected_job_is_not_resubmitted(capsys):
    from hermes_cli import model_setup_flows_local as flow
    from hermes_cli.local_model_client import LocalModelClientError

    class Gateway:
        def get(self, path):
            raise LocalModelClientError('outcome is unknown')

        def post(self, path, body):
            pytest.fail('ambiguous job was resubmitted')

    assert flow._follow_job(None, Gateway(), 'job-1') == 'unknown'
    assert 'outcome is unknown' in capsys.readouterr().out
