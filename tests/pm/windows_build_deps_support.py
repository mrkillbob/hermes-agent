"""Shared native PowerShell invocation for Windows build prerequisite tests."""
from pathlib import Path
import os
import shutil
import subprocess


HELPER = Path(__file__).resolve().parents[2] / "scripts" / "windows-build-deps.ps1"


def _powershell(script, *args, env=None):
    env = dict(os.environ) if env is None else env
    env.setdefault("SystemRoot", r"C:\Windows")
    env.setdefault("ComSpec", str(Path(env["SystemRoot"]) / "System32/cmd.exe"))
    env.setdefault("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    env.setdefault("SystemDrive", Path(env["SystemRoot"]).drive)
    shell = shutil.which("powershell") or str(
        Path(env["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=180, check=False,
    )
