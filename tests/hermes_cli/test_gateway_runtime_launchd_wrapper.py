"""The installer and recovery verifier agree without executing wrapper scripts."""
import copy
from pathlib import Path

import pytest


def installed_definition(tmp_path, monkeypatch, profile=False, *, runtime=False):
    import plistlib
    from hermes_cli import gateway as gw, _launchers
    from hermes_cli.gateway_launchd import generate_launchd_plist

    home = tmp_path / '.hermes'
    if profile:
        home = home / 'profiles' / 'worker'
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    # Exercise the real current generator's PM branch; no launcher is executed.
    root = tmp_path / "source space's $literal"
    monkeypatch.setattr(gw, 'PROJECT_ROOT', root)
    selected_python = None if runtime else tmp_path / 'python3'
    monkeypatch.setattr(_launchers, 'resolve_store_python', lambda root: selected_python)
    monkeypatch.setattr(gw, '_profile_arg', lambda: '--profile worker' if profile else '')
    return home, plistlib.loads(generate_launchd_plist().encode())


def loaded_definition(definition):
    argv = definition['ProgramArguments']
    env = definition['EnvironmentVariables']
    return ('gui/123/ai.hermes.gateway = {\n\tuid = 123\n\tusername = owner\n'
            '\tprogram = ' + argv[0] + '\n\targuments = {\n'
            + '\n'.join('\t\t' + arg for arg in argv) + '\n\t}\n\tenvironment = {\n'
            + '\n'.join('\t\t' + key + ' => ' + value for key, value in env.items())
            + '\n\t}\n}')


@pytest.mark.parametrize('profile', [False, True])
@pytest.mark.parametrize('loaded', [False, True])
def test_current_installer_wrapper_is_verified(tmp_path, monkeypatch, profile, loaded):
    from hermes_cli.gateway_runtime_service_identity import verify_launchd_loaded, verify_launchd_plist
    home, definition = installed_definition(tmp_path, monkeypatch, profile)
    if loaded:
        verify_launchd_loaded(loaded_definition(definition), home, uid=123, username='owner')
    else:
        verify_launchd_plist(definition, definition['Label'], home)


@pytest.mark.parametrize('profile', [False, True])
@pytest.mark.parametrize('loaded', [False, True])
def test_runtime_installer_wrapper_is_verified(tmp_path, monkeypatch, profile, loaded):
    from hermes_cli.gateway_runtime_service_identity import verify_launchd_loaded, verify_launchd_plist
    home, definition = installed_definition(tmp_path, monkeypatch, profile, runtime=True)
    if loaded:
        verify_launchd_loaded(loaded_definition(definition), home, uid=123, username='owner')
    else:
        verify_launchd_plist(definition, definition['Label'], home)


@pytest.mark.parametrize('loaded', [False, True])
def test_modified_runtime_bootstrap_is_refused(tmp_path, monkeypatch, loaded):
    from hermes_cli.gateway_launchd import (
        _timestamped_stderr_gateway_command,
        launchd_program_arguments,
    )
    from hermes_cli.gateway_runtime_service_identity import verify_launchd_loaded, verify_launchd_plist
    home, definition = installed_definition(tmp_path, monkeypatch, runtime=True)
    command = _timestamped_stderr_gateway_command(home / 'logs/gateway.error.log', external_supervisor=True)
    # The outer timestamp shim is argv[0:4], then its three arguments, then
    # the child runtime command; mutate the child's Python bootstrap itself.
    child_argv_index = 4 + 3
    child_bootstrap_index = child_argv_index + 3
    command[child_bootstrap_index] += "; import sys; sys.exit(0)"
    definition['ProgramArguments'] = launchd_program_arguments(
        command, home / 'logs/gateway.log', home / 'logs/gateway.error.log')
    with pytest.raises(ValueError, match='service_identity_unverified'):
        if loaded:
            verify_launchd_loaded(loaded_definition(definition), home, uid=123, username='owner')
        else:
            verify_launchd_plist(definition, definition['Label'], home)


@pytest.mark.parametrize('case,reason', [
    ('script', 'service_identity_unverified'), ('shell', 'service_identity_unverified'),
    ('stdout', 'service_identity_unverified'), ('stderr', 'service_identity_unverified'),
    ('timestamp_log', 'service_identity_unverified'), ('outer_executable', 'service_identity_unverified'),
    ('inner_executable', 'service_identity_unverified'), ('extra_command', 'service_identity_unverified'),
    ('profile', 'profile_mismatch'),
])
@pytest.mark.parametrize('loaded', [False, True])
def test_changed_wrapper_or_binding_is_refused(tmp_path, monkeypatch, case, reason, loaded):
    import json
    import shlex
    from hermes_cli.gateway_launchd import launchd_program_arguments
    from hermes_cli.gateway_runtime_service_identity import verify_launchd_loaded, verify_launchd_plist

    home, definition = installed_definition(tmp_path, monkeypatch)
    definition = copy.deepcopy(definition)
    argv = definition['ProgramArguments']
    launcher = str(tmp_path / "source space's $literal" / '.hermes/bin/hermes')
    inner = [launcher, '--run-module', 'hermes_cli.stderr_timestamp', '--error-log',
             str(home / 'logs/gateway.error.log'), '--', launcher, 'gateway', 'run', '--external-supervisor']
    if case == 'script':
        argv[-1] += '; $.exit(0);'
    elif case in {'shell', 'extra_command'}:
        shell = 'exec ' + shlex.join(inner) + ' >> ' + shlex.quote(str(home / 'logs/gateway.log')) + ' 2>> ' + shlex.quote(str(home / 'logs/gateway.error.log'))
        replacement = shell.replace('exec ', '', 1) if case == 'shell' else shell + '; echo extra'
        argv[-1] = argv[-1].replace(json.dumps(shell), json.dumps(replacement))
    elif case == 'outer_executable':
        argv[0] = '/tmp/osascript'
    else:
        stdout = tmp_path / 'foreign.log' if case == 'stdout' else home / 'logs/gateway.log'
        stderr = tmp_path / 'foreign.log' if case == 'stderr' else home / 'logs/gateway.error.log'
        if case == 'timestamp_log':
            inner[4] = str(tmp_path / 'foreign.log')
        if case == 'inner_executable':
            inner[6] = '/bin/echo'
        if case == 'profile':
            inner += ['--profile', 'stranger']
        definition['ProgramArguments'] = launchd_program_arguments(inner, stdout, stderr)
    with pytest.raises(ValueError, match=reason):
        if loaded:
            output = loaded_definition(definition)
            verify_launchd_loaded(output, home, uid=123, username='owner')
        else:
            verify_launchd_plist(definition, definition['Label'], home)


@pytest.mark.parametrize('case,reason', [
    ('home', 'profile_mismatch'), ('supervision', 'service_identity_unverified'),
    ('account', 'service_account_mismatch'),
])
@pytest.mark.parametrize('loaded', [False, True])
def test_wrapper_retains_service_binding_checks(tmp_path, monkeypatch, case, reason, loaded):
    from hermes_cli.gateway_runtime_service_identity import verify_launchd_loaded, verify_launchd_plist
    home, definition = installed_definition(tmp_path, monkeypatch)
    if case == 'home':
        definition['EnvironmentVariables']['HERMES_HOME'] = str(tmp_path / 'other')
    elif case == 'supervision':
        definition['EnvironmentVariables'].pop('HERMES_SUPERVISED_CHILD', None)
    elif not loaded:
        definition['UserName'] = 'other'
        reason = 'service_identity_unverified'
    with pytest.raises(ValueError, match=reason):
        if loaded:
            output = loaded_definition(definition)
            if case == 'account':
                output = output.replace('username = owner', 'username = other')
            verify_launchd_loaded(output, home, uid=123, username='owner')
        else:
            verify_launchd_plist(definition, definition['Label'], home)
