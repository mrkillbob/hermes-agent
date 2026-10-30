"""Exercise the Windows supervisor's shared process and launcher resolution."""
import json
from pathlib import Path
import subprocess

import pytest


@pytest.mark.platforms('windows')
def test_desktop_identity_ignores_argv_quoting_and_excludes_electron_children():
    helper = Path(__file__).resolve().parents[2] / 'scripts' / 'fleet_desktop_runtime.ps1'
    quoted_helper = str(helper).replace("'", "''")
    command = r'''
$desktop = 'C:\Program Files\Hermes\Hermes.exe'
$processes = @(
 [pscustomobject]@{Name='Hermes.exe';ExecutablePath=$desktop;ProcessId=12;ParentProcessId=11;CommandLine='child'},
 [pscustomobject]@{Name='Hermes.exe';ExecutablePath=$desktop;ProcessId=11;ParentProcessId=4;CommandLine=$desktop},
 [pscustomobject]@{Name='Hermes.exe';ExecutablePath='C:\other\Hermes.exe';ProcessId=13;ParentProcessId=4},
 [pscustomobject]@{Name='Hermes.exe';ExecutablePath=$desktop;ProcessId=14;ParentProcessId=99;CommandLine=('"'+$desktop+'" --type=gpu-process')}
)
$main = Get-FleetDesktopMainProcess $processes $desktop
$quoted = Get-FleetDesktopMainProcess @([pscustomobject]@{Name='Hermes.exe';ExecutablePath=$desktop;ProcessId=15;ParentProcessId=4;CommandLine=('"'+$desktop+'"')}) $desktop
$orphan = Get-FleetDesktopMainProcess @($processes | Where-Object {$_.ProcessId -eq 14}) $desktop
$missing = Get-FleetDesktopMainProcess $processes 'C:\absent\Hermes.exe'
@{main=$main.ProcessId;quoted=$quoted.ProcessId;missing=($null -eq $missing);orphan=($null -eq $orphan)} | ConvertTo-Json -Compress
'''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                             f". '{quoted_helper}';" + command], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {'main': 11, 'quoted': 15, 'missing': True, 'orphan': True}


@pytest.mark.platforms('windows')
def test_launcher_resolves_existing_cmd_and_prefers_exe(tmp_path):
    helper = Path(__file__).resolve().parents[2] / 'scripts' / 'fleet_desktop_runtime.ps1'
    quoted_helper = str(helper).replace("'", "''")
    quoted_root = str(tmp_path).replace("'", "''")
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    cmd = bin_dir / 'hermes.cmd'
    cmd.write_text('@echo off\n')

    def resolve():
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 f". '{quoted_helper}'; Resolve-FleetHermesExecutable '{quoted_root}'"],
                                capture_output=True, text=True, check=True)
        return Path(result.stdout.strip())

    assert resolve() == cmd
    exe = bin_dir / 'hermes.exe'
    exe.touch()
    assert resolve() == exe
