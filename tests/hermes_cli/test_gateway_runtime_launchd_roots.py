"""A fake HOME must not make Desktop adopt or start the account's installed gateway."""
import os
import plistlib
import subprocess
import time
from types import SimpleNamespace

import pytest


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('loaded', [False, True])
@pytest.mark.parametrize('binding,reason', [('foreign', None), ('profile', 'profile_mismatch'), ('missing', 'service_identity_unverified')])
def test_launchd_only_claims_services_bound_to_requested_root(tmp_path, monkeypatch, loaded, binding, reason):
    import pwd
    from hermes_cli import gateway_runtime_service as service

    account_home = tmp_path / 'account'
    fake_home = tmp_path / 'fixture'
    requested = fake_home / '.hermes'
    requested.mkdir(parents=True)
    monkeypatch.setenv('HOME', str(fake_home))
    monkeypatch.setenv('HERMES_HOME', str(requested))
    monkeypatch.setattr(pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_dir=str(account_home), pw_name='owner'))
    configured = account_home / '.hermes' if binding == 'foreign' else requested / 'profiles' / 'wrong'
    env = {'HERMES_SUPERVISED_CHILD': '1'}
    if binding != 'missing':
        env['HERMES_HOME'] = str(configured)
    label = 'ai.hermes.gateway'
    definition = {'Label': label, 'EnvironmentVariables': env, 'ProgramArguments': ['/bin/echo', 'untrusted']}
    plist = account_home / 'Library/LaunchAgents' / (label + '.plist')
    plist.parent.mkdir(parents=True)
    original = plistlib.dumps(definition)
    plist.write_bytes(original)
    output = '\n'.join(['job = {', '\tprogram = /bin/echo', '\targuments = {', '\t\t/bin/echo', '\t\tuntrusted', '\t}',
                        '\tenvironment = {', *['\t\t' + k + ' => ' + v for k, v in env.items()], '\t}', '}'])
    calls = []

    def query(argv, deadline):
        calls.append(argv)
        assert argv[:2] == ['launchctl', 'print']
        present = loaded and argv[2].startswith('gui/')
        return subprocess.CompletedProcess(argv, 0 if present else 113, output if present else '', '')

    monkeypatch.setattr(service, '_run', query)
    if reason:
        with pytest.raises(service.RuntimeStartError, match=reason):
            service._launchd(requested, time.monotonic() + 5)
    else:
        assert service._launchd(requested, time.monotonic() + 5) is None
    assert len(calls) == 2
    assert plist.read_bytes() == original
    assert not (requested / 'logs').exists()


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('bindings,expected', [
    (('foreign', 'foreign'), None),
    (('foreign', 'requested'), 'user'),
    (('requested', 'foreign'), 'gui'),
    (('requested', 'requested'), 'service_scope_conflict'),
    (('foreign', 'missing'), 'service_identity_unverified'),
])
def test_launchd_counts_only_verified_home_bindings(tmp_path, monkeypatch, bindings, expected):
    import pwd
    import sys
    from hermes_cli import gateway_runtime_service as service

    account = tmp_path / 'account'
    requested = tmp_path / 'fixture' / '.hermes'
    requested.mkdir(parents=True)
    monkeypatch.setenv('HOME', str(requested.parent))
    monkeypatch.setenv('HERMES_HOME', str(requested))
    monkeypatch.setattr(pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_dir=str(account), pw_name='owner'))
    # A stale on-disk definition must never override either loaded domain.
    plist = account / 'Library/LaunchAgents/ai.hermes.gateway.plist'
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({'Label': 'ai.hermes.gateway',
        'EnvironmentVariables': {'HERMES_HOME': str(requested), 'HERMES_SUPERVISED_CHILD': '1'},
        'ProgramArguments': ['/bin/echo', 'stale']}))
    calls = []

    def query(argv, deadline):
        calls.append(argv)
        assert argv[:2] == ['launchctl', 'print']
        binding = bindings[0 if argv[2].startswith('gui/') else 1]
        home = requested if binding == 'requested' else account / '.hermes'
        env = '\t\tHERMES_SUPERVISED_CHILD => 1\n'
        if binding != 'missing':
            env += '\t\tHERMES_HOME => ' + str(home) + '\n'
        argv_lines = '\n'.join('\t\t' + a for a in [sys.executable, '-m', 'hermes_cli.main', 'gateway', 'run'])
        output = 'job = {\n\tprogram = ' + sys.executable + '\n\targuments = {\n' + argv_lines + '\n\t}\n\tenvironment = {\n' + env + '\t}\n}'
        return subprocess.CompletedProcess(argv, 0, output, '')

    monkeypatch.setattr(service, '_run', query)
    if expected and expected.startswith('service_'):
        with pytest.raises(service.RuntimeStartError, match=expected):
            service._launchd(requested, time.monotonic() + 5)
    else:
        result = service._launchd(requested, time.monotonic() + 5)
        if expected is None:
            assert result is None
        else:
            assert result.start_argv == ('launchctl', 'kickstart', f'{expected}/{os.getuid()}/ai.hermes.gateway')
    assert len(calls) == 2
    assert not (requested / 'logs').exists()
