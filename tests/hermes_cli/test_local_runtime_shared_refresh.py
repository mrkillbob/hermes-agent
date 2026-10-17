"""Shared inference transitions cannot be mistaken for an idle router."""

import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('status', ['loading', 'unloading', 'unknown', 'sleeping'])
def test_implicit_refresh_preserves_requests_during_model_transition(monkeypatch, status):
    from hermes_cli.local_runtime import bootstrap
    from hermes_cli.local_runtime.supervisor import LlamaServerSupervisor

    sup = LlamaServerSupervisor.__new__(LlamaServerSupervisor)
    monkeypatch.setattr(sup, 'models', lambda: {'first-request': status})
    stopped = []
    monkeypatch.setattr(sup, 'stop', lambda: stopped.append('interrupted'))
    owner = SimpleNamespace(stopping=threading.Event(), ensure=lambda **kwargs: sup)
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', sup)
    monkeypatch.setattr(bootstrap, '_SERVING_OWNER', owner)
    assert bootstrap.refresh_local_runtime() is False
    assert stopped == []


def test_idle_snapshot_does_not_grant_authority_to_interrupt_shared_requests(monkeypatch):
    from hermes_cli.local_runtime import bootstrap

    calls = []
    sup = SimpleNamespace(is_idle=lambda: calls.append('snapshot') or True,
                          stop=lambda: calls.append('interrupted'))
    owner = SimpleNamespace(stopping=threading.Event(), ensure=lambda **kwargs: sup)
    monkeypatch.setattr(bootstrap, '_SUPERVISOR', sup)
    monkeypatch.setattr(bootstrap, '_SERVING_OWNER', owner)
    assert bootstrap.refresh_local_runtime() is False
    assert calls == []


def test_engine_install_reports_staged_build_without_restarting_owner(monkeypatch):
    from hermes_cli.web_routers import local_models as lm

    changes = []
    monkeypatch.setattr(lm, '_runtime_target', lambda backend: ('fixture-tag', 'cpu'))
    monkeypatch.setattr(lm, '_install_engine_job', lambda job, backend: SimpleNamespace(tag='fixture-tag'))
    monkeypatch.setattr(lm, '_spawn_job', lambda job, name, run, **kwargs: run())
    monkeypatch.setattr(lm.bootstrap, 'shutdown_local_runtime', lambda: changes.append('interrupted'))
    result = lm.local_models_runtime_install(lm.RuntimeInstallBody(backend='cpu'))
    job = lm._JOBS[result['job_id']]
    assert job['phase'] == 'done'
    assert 'installed' in job['detail'] and 'restart' in job['detail']
    assert changes == []
