# Run a driver outside the runner step's Job Object (dot-sourced by windows-e2e.ps1).
#
# The Actions runner puts each step's process tree in a Job Object that has no
# breakaway right (limit_flags 0). Hermes starts its gateway detached with
# CREATE_BREAKAWAY_FROM_JOB and deliberately refuses a weaker fallback, so a
# product process whose parent is inside that job gets ACCESS_DENIED
# (windows_breakaway_unavailable) the moment it has to start a gateway itself.
# A user's app is not inside such a job; only this harness is. Legs that update a
# historical release leave no gateway behind for the new app to reuse, so they
# hit it (issue 301); legs where something outside the job starts the app never do.
#
# Run 1 tried a one-shot scheduled task: the Task Scheduler also wraps its tasks in a Job Object
# without breakaway rights (limit_flags 0), so the refusal persisted. Explorer is already running in
# the interactive session and is not in the runner's job; `explorer.exe <file.cmd>` makes it start
# the file, so the process tree hangs off Explorer instead of this step. If the wrapper does not
# start, the caller runs the driver in-process as before.

$script:OutOfJobSecretNames = '(?i)(TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_KEY)'

function Test-OutOfJobLaunchWanted {
    return ($env:GITHUB_ACTIONS -eq 'true')
}

function Read-SharedTextLines([string]$Path) {
    # The wrapper appends while this reads: open with share-write and tolerate a half-written last line.
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
        $text = $reader.ReadToEnd()
    } finally { $stream.Dispose() }
    return , @($text -split "\r?\n")
}

$script:OutOfJobWrapper = @'
$ErrorActionPreference = 'Continue'
$stage = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $stage 'output.log'
Set-Content -LiteralPath (Join-Path $stage 'started.txt') -Value $PID
$exitCode = 98
try {
    $spec = Get-Content -LiteralPath (Join-Path $stage 'spec.json') -Raw | ConvertFrom-Json
    foreach ($entry in $spec.env.PSObject.Properties) {
        [Environment]::SetEnvironmentVariable($entry.Name, [string]$entry.Value, 'Process')
    }
    Set-Location -LiteralPath $spec.cwd
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
    $driverArgs = @($spec.args)
    & $spec.file @driverArgs 2>&1 | ForEach-Object { Add-Content -LiteralPath $log -Value "$_" -Encoding UTF8 }
    $exitCode = $LASTEXITCODE
} catch {
    Add-Content -LiteralPath $log -Value ("out-of-job wrapper failure: " + $_.Exception.Message) -Encoding UTF8
}
Set-Content -LiteralPath (Join-Path $stage 'exit.txt') -Value $exitCode
'@

# Returns the driver's exit code, or $null when the task could not be started
# (the caller then runs the driver in-process).
function Invoke-OutsideRunnerJob {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$StageDir,
        [int]$StartTimeoutSeconds = 90,
        [int]$TimeoutMinutes = 60
    )
    New-Item -ItemType Directory -Path $StageDir -Force | Out-Null
    $specPath = Join-Path $StageDir 'spec.json'
    $logPath = Join-Path $StageDir 'output.log'
    $exitPath = Join-Path $StageDir 'exit.txt'
    $startedPath = Join-Path $StageDir 'started.txt'
    $wrapperPath = Join-Path $StageDir 'wrapper.ps1'
    foreach ($stale in @($logPath, $exitPath, $startedPath)) {
        Remove-Item -LiteralPath $stale -Force -ErrorAction SilentlyContinue
    }

    # A process started by Explorer gets Explorer's environment, not this step's.
    $envMap = [ordered]@{}
    foreach ($item in (Get-ChildItem Env:)) {
        if ($item.Name -notmatch $script:OutOfJobSecretNames) { $envMap[$item.Name] = $item.Value }
    }
    $spec = [ordered]@{ file = $FilePath; args = @($ArgumentList); cwd = $WorkingDirectory; env = $envMap }
    $spec | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $specPath -Encoding UTF8
    Set-Content -LiteralPath $wrapperPath -Value $script:OutOfJobWrapper -Encoding UTF8

    # Explorer starts the .cmd; it only starts the wrapper (a ps1 would open in an editor).
    $launcherPath = Join-Path $StageDir 'launch.cmd'
    $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    Set-Content -LiteralPath $launcherPath -Encoding ASCII -Value ('@"{0}" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{1}"' -f $psExe, $wrapperPath)
    $result = $null
    $wrapperPid = $null
    try {
        Start-Process -FilePath (Join-Path $env:SystemRoot 'explorer.exe') -ArgumentList ('"{0}"' -f $launcherPath)
        Write-Host "  out-of-job: asked explorer.exe to start $launcherPath (driver runs outside the runner step's job)"
        $begin = Get-Date
        $printed = 0
        $endedAt = $null
        while ($true) {
            Start-Sleep -Seconds 2
            $finished = Test-Path -LiteralPath $exitPath
            if (Test-Path -LiteralPath $logPath) {
                try {
                    $lines = Read-SharedTextLines $logPath
                    # While running, the last element may be a partial line.
                    $complete = if ($finished) { $lines.Count } else { [Math]::Max(0, $lines.Count - 1) }
                    for ($i = $printed; $i -lt $complete; $i++) { Write-Host "  oj| $($lines[$i])" }
                    if ($complete -gt $printed) { $printed = $complete }
                } catch [System.IO.IOException] { }
            }
            if ($finished) { break }
            $elapsed = (Get-Date) - $begin
            if ($null -eq $wrapperPid -and (Test-Path -LiteralPath $startedPath)) {
                $wrapperPid = [int](Get-Content -LiteralPath $startedPath -Raw).Trim()
                Write-Host "  out-of-job: wrapper running as pid $wrapperPid"
            }
            if ($null -eq $wrapperPid -and $elapsed.TotalSeconds -gt $StartTimeoutSeconds) {
                Write-Host "  out-of-job: explorer did not start the wrapper within ${StartTimeoutSeconds}s (no interactive shell?); running the driver in-process"
                break
            }
            if ($null -ne $wrapperPid -and -not (Get-Process -Id $wrapperPid -ErrorAction SilentlyContinue)) {
                # The wrapper is gone without an exit file: it died.
                if ($null -eq $endedAt) { $endedAt = Get-Date }
                elseif (((Get-Date) - $endedAt).TotalSeconds -gt 10) { $result = 97; break }
            }
            if ($elapsed.TotalMinutes -gt ($TimeoutMinutes + 1)) { $result = 96; break }
        }
        if ($null -eq $result -and (Test-Path -LiteralPath $exitPath)) {
            $result = [int](Get-Content -LiteralPath $exitPath -Raw).Trim()
        }
    } finally {
        if ($null -ne $wrapperPid -and -not (Test-Path -LiteralPath $exitPath)) {
            Stop-Process -Id $wrapperPid -Force -ErrorAction SilentlyContinue
        }
        # The snapshot holds this step's environment.
        Remove-Item -LiteralPath $specPath -Force -ErrorAction SilentlyContinue
    }
    return $result
}
