"""Fresh native-entry witness: bounded identity/ownership reads, no task effects."""
import os
from pathlib import Path
import runpy
import sys
import traceback
import threading

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
home = Path(os.environ['HERMES_HOME'])
witness = home / 'forbidden-effect'


def refuse(effect):
    witness.write_text(effect + ': ' + ' > '.join(frame.name for frame in traceback.extract_stack(limit=16)), encoding='utf-8')
    os._exit(85)



def _registration_query_matches(command, executable, *, expected_executable, task_name):
    """Complete argv and executable identity, never a /Query prefix or basename permission."""
    import ntpath
    if not isinstance(command, (list, tuple)) or len(command) != 4 or not isinstance(command[0], str):
        return False
    expected = ntpath.normcase(expected_executable)
    if executable is not None and (not isinstance(executable, str) or ntpath.normcase(executable) != expected):
        return False
    return ntpath.normcase(command[0]) == expected and list(command[1:]) == ['/Query', '/TN', task_name]


def _windows_schtasks_executable():
    """Ask the native OS, not PATH/SystemRoot, which system executable is trusted."""
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes
    query = ctypes.WinDLL('kernel32', use_last_error=True).GetSystemDirectoryW
    query.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    query.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    size = query(buffer, len(buffer))
    return os.path.join(buffer.value, 'schtasks.exe') if 0 < size < len(buffer) else None


def _native_registration_query_allowed(command, executable):
    expected = _windows_schtasks_executable()
    if expected is None:
        return False
    from hermes_cli.gateway_windows import get_task_name
    return _registration_query_matches(command, executable,
        expected_executable=expected, task_name=get_task_name())


def audit(event, args):
    if event == 'subprocess.Popen':
        command = args[1]
        if isinstance(command, str):
            # Windows audits the list2cmdline serialization, not the input list.
            from hermes_cli._subprocess_compat import split_command_line
            command = split_command_line(command)
        if isinstance(command, (list, tuple)) and command and command[0] == 'git':
            if command[1:3] != ['-c', 'core.fsmonitor=false'] or args[3].get('GIT_NO_LAZY_FETCH') != '1':
                refuse('unfenced-git-probe')
            command = [command[0], *command[3:]]
            verb = command[1] if len(command) > 1 else ''
            if verb in {'rev-parse', 'branch', 'log', 'tag', 'describe', 'merge-base', 'rev-list', 'show'} or command[1:4] == ['--no-optional-locks', 'status', '--porcelain']:
                return  # bounded, read-only executing-code identity; no provider/plugin work
        if _native_registration_query_allowed(command, args[0]):
            return  # preserve the existing bounded supervised-owner conflict observation
    if event in {'socket.connect', 'subprocess.Popen', 'os.system', 'os.posix_spawn'}:
        refuse(event)
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if path == home / '.env' or path.is_relative_to(home / 'plugins'):
            refuse('private-source-open')


blocked = {
    ('hermes_cli.plugins', 'discover_plugins'),
    ('hermes_cli.plugins', 'get_plugin_auxiliary_tasks'),
    ('hermes_cli.boot_bootstrap', 'install_enabled'),
    ('hermes_cli.nous_auth_keepalive', 'start_nous_auth_keepalive'),
    ('gateway.run_local_runtime', 'start_owned_local_runtime'),
    ('gateway.run_bootstrap', '_start_deferred_state_db_integrity_check'),
}


def profile(frame, event, arg):
    if event == 'call' and frame.f_code.co_name in {'_run_discovery_steps', '_scan_home_layer', '_import_plugin_dir', 'start_gateway_health_export', 'reset_plugin_enabled', 'mark_running_jobs_interrupted', 'end_idle_acp_sessions', '_stop_kill_tool_subprocesses', '_write_sentinel', '_consume_clean_shutdown_marker', '_start_recover_previous_run'}:
        refuse(frame.f_code.co_name)
    if event == 'call' and (frame.f_globals.get('__name__'), frame.f_code.co_name) in blocked:
        refuse(frame.f_globals['__name__'] + '.' + frame.f_code.co_name)


def main():
    sys.addaudithook(audit)
    sys.setprofile(profile)
    threading.setprofile(profile)
    sys.argv = ['hermes', 'gateway', 'run']
    runpy.run_module('hermes_cli.main', run_name='__main__')


if __name__ == '__main__':
    main()
