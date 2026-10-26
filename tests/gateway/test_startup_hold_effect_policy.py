"""The native witness permits ownership reads without permitting service mutation."""
import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from tests.gateway.fixtures.startup_hold_peer import (
    _native_registration_query_allowed, _registration_query_matches, _windows_schtasks_executable,
)

PROGRAM = r'C:\Windows\System32\schtasks.exe'
TASK = 'Hermes_Gateway_guarded'


@pytest.mark.platforms('any')
@pytest.mark.parametrize('task', ['Hermes_Gateway', TASK, 'Hermes_Gateway_abcdef12'])
@pytest.mark.parametrize('executable', [None, PROGRAM])
def test_only_complete_current_registration_query_is_allowed(task, executable):
    assert _registration_query_matches([PROGRAM, '/Query', '/TN', task], executable,
                                       expected_executable=PROGRAM, task_name=task)


@pytest.mark.platforms('any')
@pytest.mark.parametrize('command,executable', [
    ([PROGRAM, '/Run', '/TN', TASK], None),
    ([PROGRAM, '/Create', '/TN', TASK], None),
    ([PROGRAM, '/Delete', '/TN', TASK], None),
    ([PROGRAM, '/End', '/TN', TASK], None),
    ([PROGRAM, '/Change', '/TN', TASK], None),
    ([PROGRAM, '/Query'], None),
    ([PROGRAM, '/Query', '/FO', 'CSV'], None),
    ([PROGRAM, '/Query', '/TN', 'Hermes_Gateway_other'], None),
    ([PROGRAM, '/Query', '/TN', TASK, '/S', 'remote'], None),
    ([PROGRAM, '/Query', '/TN', TASK, '/U', 'user'], None),
    ([PROGRAM, '/Query', '/TN', TASK, '/P', 'password'], None),
    ([PROGRAM, '/Query', '/TN', TASK, '/XML'], None),
    ([PROGRAM, '/Query', '/TN', TASK, '/V'], None),
    ([PROGRAM, '/Query', '/TN', TASK, '/FO', 'LIST'], None),
    ([PROGRAM, '/Query', '/TN', TASK, 'extra'], None),
    ([r'C:\untrusted\schtasks.exe', '/Query', '/TN', TASK], None),
    (['schtasks.exe', '/Query', '/TN', TASK], None),
    (['cmd.exe', '/c', PROGRAM, '/Query', '/TN', TASK], None),
    ([PROGRAM, '/Query', '/TN', TASK], r'C:\untrusted\schtasks.exe'),
    ([PROGRAM, '/Query', '/TN', TASK], 'cmd.exe'),
    ('schtasks.exe /Query /TN ' + TASK, None),
])
def test_mutations_remote_queries_and_other_executables_remain_forbidden(command, executable):
    assert not _registration_query_matches(command, executable,
                                           expected_executable=PROGRAM, task_name=TASK)


@pytest.mark.platforms('posix')
def test_windows_query_exception_is_unavailable_on_this_native_host():
    assert not _native_registration_query_allowed([PROGRAM, '/Query', '/TN', TASK], None)


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('kind', ['root', 'profile', 'custom'])
@pytest.mark.parametrize('outcome', ['present', 'absent', 'timeout'])
def test_real_registration_helper_is_bounded_nonmutating_and_profile_scoped(tmp_path, monkeypatch, kind, outcome):
    from hermes_cli import gateway_windows
    import hermes_constants

    root = tmp_path / 'native-home'
    home = {'root': root, 'profile': root / 'profiles' / 'guarded',
            'custom': tmp_path / 'other-home'}[kind]
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(hermes_constants, 'account_default_hermes_home', lambda: root)
    expected_task = {'root': 'Hermes_Gateway', 'profile': TASK,
                     'custom': 'Hermes_Gateway_' + hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:8]}[kind]
    assert gateway_windows.get_task_name() == expected_task
    program = _windows_schtasks_executable()
    assert Path(program).is_file()
    calls = []

    def run(command, **options):
        calls.append((command, options))
        assert _native_registration_query_allowed(command, options.get('executable'))
        assert _registration_query_matches(command, options.get('executable'),
                                           expected_executable=program, task_name=expected_task)
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(command, options['timeout'])
        return SimpleNamespace(returncode=0 if outcome == 'present' else 1, stdout=b'', stderr=b'')

    monkeypatch.setattr(gateway_windows.subprocess, 'run', run)
    assert gateway_windows.is_task_registered() is (outcome == 'present')
    assert len(calls) == 1  # failures never start a fallback helper or change registration
    _command, options = calls[0]
    assert options['timeout'] == 15
    assert options['capture_output'] is True
    assert options['text'] is False
    assert options['check'] is False
    assert options.get('shell', False) is False
    assert options['creationflags'] & subprocess.CREATE_NO_WINDOW


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('substituted_root', [r'C:\untrusted', r'\\untrusted\share'])
def test_query_identity_comes_from_native_os_not_substituted_environment(monkeypatch, substituted_root):
    from hermes_cli import gateway_windows
    program = _windows_schtasks_executable()
    task = gateway_windows.get_task_name()
    monkeypatch.setenv('SystemRoot', substituted_root)
    assert _native_registration_query_allowed([program, '/Query', '/TN', task], None)
    fake = str(Path(substituted_root) / 'System32' / 'schtasks.exe')
    assert not _native_registration_query_allowed([fake, '/Query', '/TN', task], None)
