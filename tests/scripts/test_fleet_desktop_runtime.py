"""Exercise the Windows supervisor's shared process and launcher resolution."""
import json
from pathlib import Path
import subprocess

import pytest


@pytest.mark.platforms('windows')
def test_desktop_identity_ignores_argv_quoting_and_excludes_electron_children():
    helper = Path(__file__).resolve().parents[2] / 'scripts' / 'fleet_desktop_runtime.ps1'
    command = r'''
$desktop = 'C:\Program Files\Hermes\Hermes.exe'
$processes = @(
 [pscustomobject]@{Name='Hermes.exe';ExecutablePath=$desktop;ProcessId=12;ParentProcessId=11;CommandLine='child'},
 [pscustomobject]@{Name='Hermes.exe';ExecutablePath=$desktop;ProcessId=11;ParentProcessId=4;CommandLine=$desktop},
 [pscustomobject]@{Name='Hermes.exe';ExecutablePath='C:\other\Hermes.exe';ProcessId=13;ParentProcessId=4}
)
$main = Get-FleetDesktopMainProcess $processes $desktop
$missing = Get-FleetDesktopMainProcess $processes 'C:\absent\Hermes.exe'
@{main=$main.ProcessId;missing=($null -eq $missing)} | ConvertTo-Json -Compress
'''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                             f". '{helper}';" + command], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {'main': 11, 'missing': True}


@pytest.mark.platforms('windows')
def test_launcher_resolves_existing_cmd_and_prefers_exe(tmp_path):
    helper = Path(__file__).resolve().parents[2] / 'scripts' / 'fleet_desktop_runtime.ps1'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    cmd = bin_dir / 'hermes.cmd'
    cmd.write_text('@echo off\n')

    def resolve():
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 f". '{helper}'; Resolve-FleetHermesExecutable '{tmp_path}'"],
                                capture_output=True, text=True, check=True)
        return Path(result.stdout.strip())

    assert resolve() == cmd
    exe = bin_dir / 'hermes.exe'
    exe.touch()
    assert resolve() == exe
