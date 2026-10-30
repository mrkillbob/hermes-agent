"""Native build-command failure and executable-path handling."""
import pytest

from tests.pm.windows_build_deps_support import HELPER, _powershell


pytestmark = pytest.mark.platforms("windows")


def test_native_build_command_preserves_failures_and_spaces(tmp_path):
    script = tmp_path / "native.ps1"
    script.write_text(r'''
param([string]$Helper, [string]$Root)
$ErrorActionPreference = 'Stop'
. $Helper
$command = Join-Path $Root 'child with spaces.cmd'
[IO.File]::WriteAllText($command, "@echo off`r`necho native-progress 1>&2`r`nexit /b 19`r`n")
$failed = $false
try { Invoke-HermesBuildCommand $command @() } catch {
    if ($_.Exception.Message -notmatch 'exit code 19') { throw }
    $failed = $true
}
if (-not $failed -or $ErrorActionPreference -ne 'Stop') { throw 'failure or shell preference was lost' }
[IO.File]::WriteAllText($command, "@echo off`r`necho native-progress 1>&2`r`nexit /b 0`r`n")
Invoke-HermesBuildCommand $command @()
$failed = $false
try { Invoke-HermesBuildCommand (Join-Path $Root 'absent.exe') @() } catch { $failed = $true }
if (-not $failed) { throw 'missing executable was accepted after a successful command' }
$a = Join-Path $Root 'cmd'
$b = Join-Path $Root 'bin'
New-Item -ItemType Directory -Force $a, $b | Out-Null
Set-Content -Path (Join-Path $a 'git.exe') -Value 'first' -Encoding ascii
Set-Content -Path (Join-Path $b 'git.exe') -Value 'second' -Encoding ascii
$env:PATH = "$a;$b;$env:PATH"
$resolved = @(Get-Command git -CommandType Application -ErrorAction Stop | Select-Object -First 1)[0].Source
if ($resolved -ne (Join-Path $a 'git.exe')) { throw "resolved every git.exe: $resolved" }
# The call operator must receive that one path, not both paths joined by a space.
$executable = $resolved
if ($executable -isnot [string] -or $executable.Contains(' ')) { throw "joined path leaked: $executable" }
Write-Output 'PASS'
''', encoding="utf-8")
    # Use the shared helper's existing cold-runner budget.
    result = _powershell(script, HELPER, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
