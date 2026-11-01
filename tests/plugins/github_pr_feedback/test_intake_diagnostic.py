"""Passive diagnostics through disposable native plugin discovery and caller binding."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import hermes_yaml as yaml
import pytest
from hermes_cli.plugins import PluginManager
from hermes_cli.plugin_session import attach_cli_handler


@pytest.fixture
def diagnostic(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    plugins = home / 'plugins'
    plugins.mkdir(parents=True)
    source = Path(__file__).resolve().parents[3] / 'plugins/github-pr-feedback'
    shutil.copytree(source, plugins / 'github-pr-feedback')
    bundled = tmp_path / 'bundled'
    bundled.mkdir()
    repo = tmp_path / 'caller'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin',
                    'https://github.com/mrkillbob/hermes-agent.git'], check=True)
    settings = {'enabled': False, 'auto_dispatch': False, 'intake_only': {'enabled': True},
                'repositories': [{'base_repository': 'mrkillbob/hermes-agent',
                                  'local_path': str(repo)}]}
    (home / 'config.yaml').write_text(yaml.safe_dump({'plugins': {
        'enabled': ['github-pr-feedback'],
        'entries': {'github-pr-feedback': {'settings': settings}}}}))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_BUNDLED_PLUGINS', str(bundled))
    monkeypatch.chdir(repo)
    manager = PluginManager()
    manager.discover_and_load()
    loaded = manager._plugins['github-pr-feedback']
    assert loaded.enabled and loaded.error is None
    cli = loaded.module._module('intake_cli')
    context = loaded.module._module('intake_context')
    intake = loaded.module._module('intake_only')
    binding = loaded.module._module('caller_repository')
    forbidden = []
    for module, name in ((cli, 'automation_client'), (cli, 'IntakeKanban'),
                         (cli, 'scan_intake'), (intake, 'ReceiptLedger')):
        sentinel = Mock(side_effect=AssertionError('write or network path reached'))
        monkeypatch.setattr(module, name, sentinel)
        forbidden.append(sentinel)
    adapter = loaded.module._module('intake_kanban')
    create = Mock(side_effect=AssertionError('card create reached'))
    monkeypatch.setattr(adapter.IntakeKanban, 'create', create)
    forbidden.append(create)
    calls, failure = [], {'phase': None}

    def step(phase, result=None):
        def invoke(*args):
            assert binding.current_repository().root == repo.resolve()
            calls.append(phase)
            if failure['phase'] == phase:
                raise RuntimeError('synthetic-secret /private/path')
            return result
        return invoke

    monkeypatch.setattr(context, 'registered_control_root', step('control_root', home))
    monkeypatch.setattr(cli, 'load_intake_policy', step('policy',
        SimpleNamespace(check=step('time_policy'))))
    monkeypatch.setattr(context, 'require_context', step('passive_context'))
    command = manager._cli_commands['github-pr-feedback']
    parser = argparse.ArgumentParser()
    command['setup_fn'](parser)
    attach_cli_handler(parser, command['handler_fn'])
    args = parser.parse_args(['diagnose-intake', '--repository', 'mrkillbob/hermes-agent'])
    yield args.func, args, calls, failure, repo
    for sentinel in forbidden:
        sentinel.assert_not_called()


@pytest.mark.parametrize('phase', [None, 'control_root', 'policy', 'time_policy', 'passive_context'])
def test_diagnostic_is_bound_passive_and_redacted(diagnostic, capsys, phase):
    handler, args, calls, failure, _ = diagnostic
    failure['phase'] = phase
    assert handler(args) == (1 if phase else 0)
    output = capsys.readouterr().out
    result = json.loads(output)
    phases = ['control_root', 'policy', 'time_policy', 'passive_context']
    assert calls == (phases[:phases.index(phase) + 1] if phase else phases)
    assert result['created'] == 0 and result['historical_cause'] == 'unknown'
    assert result['not_checked'] == ['credentials', 'github', 'receipts', 'card_write']
    assert result['status'] == ('rejected' if phase else 'passive_checks_passed')
    if phase:
        assert result['reason'] == phase + '_unavailable'
    assert 'synthetic-secret' not in output and '/private/path' not in output


@pytest.mark.parametrize('kind', ['forbidden', 'unbound'])
def test_diagnostic_cannot_skip_frontdoor_guards(diagnostic, capsys, monkeypatch, kind):
    handler, args, calls, _, repo = diagnostic
    if kind == 'forbidden':
        args.dispatch = True
    else:
        monkeypatch.chdir(repo.parent)
    assert handler(args) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'rejected'
    assert calls == []
