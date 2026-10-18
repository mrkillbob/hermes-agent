"""Native inert proof of the updater job's cancellation and daemon lifetime contract."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import time

import psutil
import pytest

from tests.scripts.desktop_update.windows_handoff_support import POWERSHELL, ROOT

pytestmark = pytest.mark.platforms("windows")

WORKER = r'''
import json, pathlib, subprocess, sys, time
root, home = map(pathlib.Path, sys.argv[1:3])
(home/"worker-entered").write_text("entered")
phase = "imports"
def worker_error(kind, error, traceback):
    frames = []
    while traceback is not None:
        frame = traceback.tb_frame
        frames.append({"module": frame.f_globals.get("__name__"),
                       "function": frame.f_code.co_name, "line": traceback.tb_lineno})
        traceback = traceback.tb_next
    report = {"type": kind.__name__, "phase": phase, "frames": frames[-8:]}
    if isinstance(error, ModuleNotFoundError):
        report["missing_module"] = error.name
    (home/"worker-error.json").write_text(json.dumps(report))
try:
    sys.path.insert(0, sys.argv[3])
    sys.path.insert(0, str(root))
    from hermes_cli import gateway_runtime_start as start
    from hermes_cli.gateway_runtime_service import RuntimeStartError
    from hermes_platform.host.windows_job import current_process_job_limits
    phase = "ordinary-launch"
    real_popen = subprocess.Popen
    program = "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); p.write_text('ready'); release=pathlib.Path(sys.argv[2]);\nwhile not release.exists(): time.sleep(0.02)"
    ordinary = real_popen([sys.executable, '-I', '-c', program, str(home/'ordinary-ready'), str(home/'release')], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    calls = []
    def inert(argv, **kwargs):
        calls.append(kwargs['creationflags'])
        return real_popen([argv[0], '-I', '-c', program, str(home/'daemon-ready'), str(home/'release')], **kwargs)
    start.subprocess.Popen = inert
    report = {'job': current_process_job_limits(), 'ordinary': ordinary.pid}
    phase = "gateway-launch"
    try:
        child = start.spawn_unmanaged_gateway(home, deadline=time.monotonic()+20)
        report['daemon'] = child.pid
    except RuntimeStartError as exc:
        report['refusal'] = {'reason': exc.reason, 'errno': getattr(exc.__cause__, 'errno', None), 'winerror': getattr(exc.__cause__, 'winerror', None)}
    report['attempts'] = len(calls)
    report['creation_flags'] = calls
    (home/'report.json').write_text(json.dumps(report))
    while not (home/'release').exists(): time.sleep(0.05)
except BaseException as error:
    worker_error(type(error), error, error.__traceback__)
    raise SystemExit(1) from None
'''


def _wait(path: Path, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists() and time.monotonic() < deadline:
        error = path.parent / "worker-error.json"
        assert not error.exists(), error.read_text() if error.exists() else ""
        time.sleep(0.02)
    assert path.exists(), f"Inert job probe did not produce {path.name}"


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def test_update_step_allows_explicit_daemon_escape_but_cancels_ordinary_children(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER)
    python = getattr(sys, "_base_executable", sys.executable)
    args = subprocess.list2cmdline(["-I", str(worker), str(ROOT), str(tmp_path), sysconfig.get_paths()["purelib"]])
    helper = ROOT / "scripts/desktop-update/update-job.ps1"
    cancel = tmp_path / "cancel"
    control = tmp_path / "control.ps1"
    control.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_ps_literal(str(helper))}\n"
        f"$s=[HermesUpdateJob]::StartAssigned({_ps_literal(python)}, {_ps_literal(args)})\n"
        "$stdout=$s.StandardOutput.ReadToEndAsync(); $stderr=$s.StandardError.ReadToEndAsync()\n"
        "try {\n [HermesUpdateJob]::Resume($s)\n"
        f" $until=[DateTime]::UtcNow.AddSeconds(90); while (-not (Test-Path -LiteralPath {_ps_literal(str(cancel))})) {{ if ([DateTime]::UtcNow -gt $until) {{ throw 'probe controller deadline' }}; Start-Sleep -Milliseconds 20 }}\n"
        " if (-not [HermesUpdateJob]::TerminateAndWait($s.Job, 1, 5000)) { throw 'contained tree did not quiesce' }\n"
        "} finally { try { $s.Process.Refresh(); if ($s.Process.HasExited) { Write-Output ('inert_worker_exit=' + $s.Process.ExitCode) } else { Write-Output 'inert_worker_running=true' } } finally { [void][HermesUpdateJob]::TerminateAndWait($s.Job, 1, 5000); [HermesUpdateJob]::Close($s.Job); if ($stdout.Wait(3000)) { [Console]::WriteLine($stdout.GetAwaiter().GetResult()) }; if ($stderr.Wait(3000)) { [Console]::Error.WriteLine($stderr.GetAwaiter().GetResult()) } } }\n"
    )
    # Only OS launch essentials enter the inert tree; CI/provider credentials do not.
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP",
    }}
    env.update(HERMES_HOME=str(tmp_path), HERMES_RUNTIME_DIR=str(tmp_path / "empty-store"))
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        folder = tmp_path / key.lower()
        folder.mkdir()
        env[key] = str(folder)
    baseline = subprocess.run([python, "-I", "-c", "pass"], env=env,
                              capture_output=True, text=True, timeout=10, check=False)
    assert baseline.returncode == 0, (baseline.returncode, baseline.stdout, baseline.stderr)
    controller = subprocess.Popen([POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(control)],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  env=env)
    daemon = None
    ordinary = None
    try:
        _wait(tmp_path / "report.json")
        report = json.loads((tmp_path / "report.json").read_text())
        ordinary = psutil.Process(report["ordinary"])
        from hermes_cli._subprocess_compat import windows_detach_flags
        assert report["attempts"] == 1, report
        assert report["creation_flags"] == [windows_detach_flags()], report
        job = report["job"]
        assert job["in_job"] is True and job["membership_error"] is None, report
        assert job["query_error"] is None, report
        if "refusal" in report:
            assert job["limit_flags"] == 0, report
            assert report["refusal"] == {
                "reason": "windows_breakaway_unavailable", "errno": 13, "winerror": 5,
            }, report
            pytest.fail(f"Resumed updater job refused the one explicit inert daemon launch: {report}")
        # A green repair must permit explicit daemon escape while keeping ordinary
        # descendants contained; silent breakaway would destroy that contract.
        assert job["limit_flags"] & 0x0800, report
        assert not job["limit_flags"] & 0x1000, report
        daemon = psutil.Process(report["daemon"])
        _wait(tmp_path / "ordinary-ready")
        _wait(tmp_path / "daemon-ready")
        cancel.touch()
        output, _ = controller.communicate(timeout=15)
        assert controller.returncode == 0, output
        ordinary.wait(timeout=5)
        assert daemon.is_running(), "Explicitly detached child died with the cancelled update job"
    finally:
        (tmp_path / "release").touch()
        cancel.touch()
        try:
            output, _ = controller.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            controller.kill()
            output, _ = controller.communicate(timeout=5)
        print("Inert worker entered:", (tmp_path / "worker-entered").exists())
        if output:
            print("Inert controller/worker output:\n" + output)
        if daemon is not None:
            try:
                daemon.wait(timeout=5)
            except psutil.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)
        if ordinary is not None:
            try:
                ordinary.wait(timeout=5)
            except psutil.TimeoutExpired:
                ordinary.kill()
                ordinary.wait(timeout=5)
