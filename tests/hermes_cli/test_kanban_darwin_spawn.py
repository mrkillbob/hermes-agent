"""Native macOS worker launch must not fork the threaded gateway."""
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

pytestmark = pytest.mark.platforms("macos")


@pytest.fixture
def managed_launch(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_dispatch as dispatch
    from hermes_cli import kanban_worker_routing, profiles
    from tools.environments import local

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("{}\n")
    workspace = tmp_path / "work space ü"
    workspace.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: home)
    monkeypatch.setattr(kanban_worker_routing, "prepare_worker_route", lambda task, *a: task)
    monkeypatch.setattr(dispatch, "_restart_safe_worker_argv", lambda task, argv: argv)
    log = tmp_path / "worker.log"

    def forbidden_fork(*args, **kwargs):
        raise AssertionError("managed macOS worker selected CPython fork_exec")

    def open_log(*args):
        # Other preparation probes happen before this final pre-launch boundary.
        monkeypatch.setattr(subprocess, "_fork_exec", forbidden_fork)
        return log.open("ab")

    monkeypatch.setattr(dispatch, "_open_worker_log", open_log)
    task = kb.Task(id="t_darwin_fixture", title="Synthetic native launch", body=None,
                   assignee="default", status="running", priority=0, created_by=None,
                   created_at=0, started_at=0, completed_at=None, workspace_kind="scratch",
                   workspace_path=str(workspace), claim_lock=None, claim_expires=None, tenant=None)

    def launch(command, extra_env=None):
        env = {"HERMES_HOME": str(home), **(extra_env or {})}
        monkeypatch.setattr(local, "build_subprocess_env", lambda **kwargs: env.copy())
        monkeypatch.setattr(dispatch, "_worker_argv", lambda *args: command)
        return dispatch._default_spawn(task, str(workspace), board="default")

    return launch, workspace, log, home


def test_managed_worker_never_forks_and_preserves_launch_contract(managed_launch, tmp_path):
    import fcntl

    launch, workspace, log, home = managed_launch
    output = tmp_path / "result.json"
    sentinel = (tmp_path / "synthetic-sentinel").open("wb")
    inherited = fcntl.fcntl(sentinel.fileno(), fcntl.F_DUPFD_CLOEXEC, 64)
    os.set_inheritable(inherited, True)
    code = (
        "import json,os,pathlib,sys; "
        "fd=int(os.environ['SENTINEL_FD']); "
        "exec('try:\\n os.fstat(fd); leaked=True\\nexcept OSError:\\n leaked=False'); "
        "data={'cwd':os.getcwd(),'home':os.environ['HERMES_HOME'],"
        "'literal':os.environ['LITERAL_VALUE'],'argv':sys.argv[1:],"
        "'session':os.getpid()==os.getsid(0),'group':os.getpid()==os.getpgrp(),'leaked':leaked,"
        "'stdin_eof':sys.stdin.buffer.read()==b''}; "
        "pathlib.Path(os.environ['OUTPUT']).write_text(json.dumps(data)); "
        "print('stdout-marker');print('stderr-marker',file=sys.stderr)"
    )
    args = ["space argument", "$(literal)", "ü", ""]
    try:
        pid = launch([sys.executable, "-I", "-c", code, *args],
                     {"OUTPUT":str(output), "SENTINEL_FD":str(inherited), "LITERAL_VALUE":"$literal value"})
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0, log.read_text()
    finally:
        os.close(inherited)
        sentinel.close()
    assert json.loads(output.read_text()) == {
        "cwd":str(workspace.resolve()), "home":str(home), "literal":"$literal value",
        "argv":args, "session":True, "group":True, "leaked":False, "stdin_eof":True,
    }
    assert {"stdout-marker", "stderr-marker"} <= set(log.read_text().splitlines())


def test_managed_worker_restores_sigpipe_default(managed_launch):
    launch, _workspace, _log, _home = managed_launch
    pid = launch(["/bin/sh", "-c", "kill -PIPE $$; exit 99"])
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGPIPE


def test_managed_exec_failure_is_synchronous(managed_launch, tmp_path):
    launch, _workspace, _log, _home = managed_launch
    with pytest.raises(RuntimeError) as caught:
        launch([str(tmp_path / "missing-executable")])
    assert isinstance(caught.value.__context__, FileNotFoundError)
    assert caught.value.__context__.errno == errno.ENOENT


@pytest.mark.parametrize("bad", ["argv", "env_value", "env_key", "cwd"])
def test_native_spawn_rejects_unrepresentable_inputs_before_child(tmp_path, bad):
    from hermes_platform.darwin_spawn import spawn_detached

    argv, env, cwd = [sys.executable, "-I", "-c", "raise SystemExit(99)"], {}, str(tmp_path)
    if bad == "argv":
        argv.append("bad\x00argument")
    elif bad == "env_value":
        env["BAD"] = "bad\x00value"
    elif bad == "env_key":
        env["BAD=KEY"] = "value"
    else:
        cwd += "\x00suffix"
    with (tmp_path / "log").open("ab") as log, pytest.raises(ValueError):
        spawn_detached(argv, cwd=cwd, env=env, log_fd=log.fileno())


def test_native_audit_refusal_prevents_child(tmp_path):
    from hermes_platform.darwin_spawn import spawn_detached

    enabled = [True]
    def refuse(event, args):
        if enabled[0] and event == "os.posix_spawn":
            raise PermissionError("synthetic native audit refusal")
    sys.addaudithook(refuse)
    try:
        with (tmp_path / "log").open("ab") as log, pytest.raises(PermissionError, match="synthetic native audit"):
            spawn_detached([sys.executable, "-I", "-c", "raise SystemExit(99)"],
                           cwd=str(tmp_path), env={}, log_fd=log.fileno())
    finally:
        enabled[0] = False


@pytest.mark.parametrize("closed", [(0,), (1,), (2,), (0, 1, 2)])
def test_native_launch_with_closed_parent_stdio(tmp_path, closed):
    # Close descriptors only in an isolated stdlib harness, never in pytest.
    result, log = tmp_path / "result", tmp_path / "log"
    harness = """
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
from hermes_platform.darwin_spawn import spawn_detached
for fd in map(int, sys.argv[4:]):
    os.close(fd)
with open(sys.argv[3], 'ab') as log:
    pid = spawn_detached([sys.executable, '-I', '-c',
        "import sys; assert sys.stdin.buffer.read()==b''; print('out'); print('err',file=sys.stderr)"],
        cwd=str(pathlib.Path(sys.argv[2]).parent), env={}, log_fd=log.fileno())
    _, status = os.waitpid(pid, 0)
pathlib.Path(sys.argv[2]).write_text(str(os.waitstatus_to_exitcode(status)))
os._exit(0)
"""
    process = subprocess.run([sys.executable, "-I", "-c", harness,
                              str(Path(__file__).resolve().parents[2]), str(result), str(log),
                              *map(str, closed)], capture_output=True, timeout=20, check=False)
    assert process.returncode == 0, process.stderr
    assert result.read_text() == "0"
    assert set(log.read_text().splitlines()) == {"out", "err"}


def test_managed_audit_events_and_refusal(managed_launch):
    launch, _workspace, _log, _home = managed_launch
    events, enabled, deny = [], [True], [False]

    def observe(event, args):
        if enabled[0] and event in {"subprocess.Popen", "os.posix_spawn"}:
            events.append(event)
            if deny[0] and event == "subprocess.Popen":
                raise PermissionError("synthetic managed audit refusal")

    sys.addaudithook(observe)
    try:
        pid = launch([sys.executable, "-I", "-c", "pass"])
        assert os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 0
        assert events[-2:] == ["subprocess.Popen", "os.posix_spawn"]
        events.clear()
        deny[0] = True
        with pytest.raises(PermissionError, match="synthetic managed audit"):
            launch([sys.executable, "-I", "-c", "raise SystemExit(99)"])
        assert events == ["subprocess.Popen"]
    finally:
        enabled[0] = False


def test_native_missing_cwd_is_synchronous_and_preserves_parent(tmp_path):
    from hermes_platform.darwin_spawn import spawn_detached

    original = os.getcwd()
    with (tmp_path / "log").open("ab") as log, pytest.raises(FileNotFoundError) as caught:
        spawn_detached([sys.executable, "-I", "-c", "raise SystemExit(99)"],
                       cwd=str(tmp_path / "missing"), env={}, log_fd=log.fileno())
    assert caught.value.errno == errno.ENOENT
    assert os.getcwd() == original


def test_native_cleanup_errors_preserve_created_pid(tmp_path, monkeypatch):
    from hermes_platform import darwin_spawn as native

    api = native._native_api()
    real_spawn, real_close = api.posix_spawn, os.close
    spawned = [False]

    def spawn(*args):
        error = real_spawn(*args)
        spawned[0] = error == 0
        return error

    def close(fd):
        real_close(fd)
        if spawned[0]:
            raise OSError(errno.EINTR, "synthetic post-launch close error")

    for name in ("posix_spawnattr_destroy", "posix_spawn_file_actions_destroy"):
        real_destroy = getattr(api, name)

        def destroy(*args, real_destroy=real_destroy):
            assert real_destroy(*args) == 0
            return errno.EINVAL

        monkeypatch.setattr(api, name, destroy)
    monkeypatch.setattr(api, "posix_spawn", spawn)
    monkeypatch.setattr(os, "close", close)
    try:
        with (tmp_path / "log").open("ab") as log:
            pid = native.spawn_detached([sys.executable, "-I", "-c", "pass"],
                                        cwd=str(tmp_path), env={}, log_fd=log.fileno())
            assert os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 0
    finally:
        spawned[0] = False


def test_native_null_close_error_does_not_leak_duplicate_or_spawn(tmp_path, monkeypatch):
    from hermes_platform import darwin_spawn as native

    real_duplicate, real_close = native._duplicate, os.close
    duplicates, reported = [], [False]

    def duplicate(fd):
        copied = real_duplicate(fd)
        duplicates.append((fd, copied))
        return copied

    def close(fd):
        real_close(fd)
        if len(duplicates) == 2 and fd == duplicates[1][0] and not reported[0]:
            reported[0] = True
            raise OSError(errno.EINTR, "synthetic closed-null error")

    def forbidden(*args):
        pytest.fail("null-input setup failure must precede native creation")

    monkeypatch.setattr(native, "_duplicate", duplicate)
    monkeypatch.setattr(native._native_api(), "posix_spawn", forbidden)
    monkeypatch.setattr(os, "close", close)
    try:
        with (tmp_path / "log").open("ab") as log, pytest.raises(OSError) as caught:
            native.spawn_detached([sys.executable, "-I", "-c", "pass"],
                                  cwd=str(tmp_path), env={}, log_fd=log.fileno())
        assert caught.value.errno == errno.EINTR
        assert reported[0] and len(duplicates) == 2
        for _, copied in duplicates:
            with pytest.raises(OSError) as closed:
                os.fstat(copied)
            assert closed.value.errno == errno.EBADF
    finally:
        for _, copied in duplicates:
            try:
                real_close(copied)
            except OSError:
                pass


def test_native_repeated_success_and_exec_failure_do_not_leak_descriptors(tmp_path):
    from hermes_platform.darwin_spawn import spawn_detached

    with (tmp_path / "log").open("ab") as log:
        before = set(os.listdir("/dev/fd"))
        for _ in range(5):
            pid = spawn_detached([sys.executable, "-I", "-c", "pass"],
                                 cwd=str(tmp_path), env={}, log_fd=log.fileno())
            assert os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 0
            with pytest.raises(FileNotFoundError):
                spawn_detached([str(tmp_path / "missing")], cwd=str(tmp_path),
                               env={}, log_fd=log.fileno())
        assert set(os.listdir("/dev/fd")) == before


def test_native_api_unavailability_never_falls_back_to_fork(tmp_path, monkeypatch):
    from hermes_platform import darwin_spawn as native

    def unavailable():
        raise OSError(errno.ENOSYS, "synthetic unavailable native API")

    def forbidden(*args, **kwargs):
        pytest.fail("native API failure must not fall back to fork")

    monkeypatch.setattr(native, "_native_api", unavailable)
    monkeypatch.setattr(subprocess, "_fork_exec", forbidden)
    with (tmp_path / "log").open("ab") as log, pytest.raises(OSError) as caught:
        native.spawn_detached([sys.executable, "-I", "-c", "pass"],
                              cwd=str(tmp_path), env={}, log_fd=log.fileno())
    assert caught.value.errno == errno.ENOSYS


@pytest.mark.parametrize("entry", ["worker", "native"])
def test_native_launch_keeps_live_system_guard_before_libc(tmp_path, monkeypatch, entry):
    from hermes_cli.kanban_worker_spawn import spawn_worker
    from hermes_platform import darwin_spawn as native

    def forbidden():
        pytest.fail("live-system guard must refuse before touching native creation")

    monkeypatch.setattr(native, "_native_api", forbidden)
    argv = [sys.executable, "-m", "hermes_cli.main", "gateway", "start"]
    with (tmp_path / "log").open("ab") as log, pytest.raises(RuntimeError, match="live-system guard"):
        if entry == "worker":
            spawn_worker(argv, cwd=str(tmp_path), stdin=subprocess.DEVNULL, stdout=log,
                         stderr=subprocess.STDOUT, env={}, start_new_session=True, creationflags=0)
        else:
            native.spawn_detached(argv, cwd=str(tmp_path), env={}, log_fd=log.fileno())


def test_native_preserves_blocked_signal_mask(tmp_path):
    # Isolate parent mask manipulation from pytest and its timeout machinery.
    result = tmp_path / "mask.json"
    harness = """
import json, os, pathlib, signal, sys
sys.path.insert(0, sys.argv[1])
from hermes_platform.darwin_spawn import spawn_detached
signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
with open(sys.argv[3], 'ab') as log:
    pid = spawn_detached([sys.executable, '-I', '-c',
        "import json,pathlib,signal,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(int(signal.SIGUSR1) in signal.pthread_sigmask(signal.SIG_BLOCK, set())))",
        sys.argv[2]], cwd=str(pathlib.Path(sys.argv[2]).parent), env={}, log_fd=log.fileno())
    _, status = os.waitpid(pid, 0)
raise SystemExit(os.waitstatus_to_exitcode(status))
"""
    process = subprocess.run([sys.executable, "-I", "-c", harness,
                              str(Path(__file__).resolve().parents[2]), str(result), str(tmp_path / "log")],
                             capture_output=True, timeout=20, check=False)
    assert process.returncode == 0, process.stderr
    assert json.loads(result.read_text()) is True


def test_native_keeps_lexical_interpreter_path_and_surrogate_env(tmp_path):
    from hermes_platform.darwin_spawn import spawn_detached

    interpreter = tmp_path / "python-link"
    interpreter.symlink_to(sys.executable)
    output = tmp_path / "result.json"
    code = ("import json,os,pathlib,sys; pathlib.Path(sys.argv[1]).write_text("
            "json.dumps([sys.executable,os.fsencode(os.environ['RAW']).hex()]))")
    with (tmp_path / "log").open("ab") as log:
        pid = spawn_detached([str(interpreter), "-I", "-c", code, str(output)],
                             cwd=str(tmp_path), env={"RAW":os.fsdecode(b"raw-\xff")}, log_fd=log.fileno())
        assert os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 0
    assert json.loads(output.read_text()) == [str(interpreter), b"raw-\xff".hex()]


def test_native_cwd_is_resolved_by_path_at_launch(tmp_path, monkeypatch):
    from hermes_platform import darwin_spawn as native

    directory = tmp_path / "cwd"
    directory.mkdir()
    (directory / "marker").write_text("original")
    real_actions = native._stdio_actions

    def replace_directory(*args):
        # File actions recorded chdir(path) already; do not pin the old directory.
        directory.rename(tmp_path / "old-cwd")
        directory.mkdir()
        (directory / "marker").write_text("replacement")
        return real_actions(*args)

    monkeypatch.setattr(native, "_stdio_actions", replace_directory)
    with (tmp_path / "log").open("ab") as log:
        pid = native.spawn_detached([sys.executable, "-I", "-c",
                                    "import pathlib; print(pathlib.Path('marker').read_text())"],
                                   cwd=str(directory), env={}, log_fd=log.fileno())
        assert os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 0
    assert (tmp_path / "log").read_text().strip() == "replacement"


@pytest.mark.parametrize("mode,expected", [(0o600, errno.EACCES), (0o700, errno.ENOEXEC)])
def test_native_exec_errors_are_synchronous(tmp_path, mode, expected):
    from hermes_platform.darwin_spawn import spawn_detached

    executable = tmp_path / "not-an-executable"
    executable.write_text("This is neither a Mach-O binary nor a shebang script.\n")
    executable.chmod(mode)
    with (tmp_path / "log").open("ab") as log, pytest.raises(OSError) as caught:
        spawn_detached([str(executable)], cwd=str(tmp_path), env={}, log_fd=log.fileno())
    assert caught.value.errno == expected
    assert caught.value.filename is None  # Native API cannot identify failed pathname.
