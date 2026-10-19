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
# A one-shot scheduled task is started by the Task Scheduler service, not by this
# step, so what it runs is outside the runner's job. The task runs as the current
# user with an interactive token so Electron still gets the desktop the AHK/Playwright
# drivers use. If the task cannot start, the caller runs the driver in-process as before.

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
Set-Content -LiteralPath (Join-Path $stage 'started.txt') -Value 'started'
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

    # A scheduled task starts from the service's environment, not this step's.
    $envMap = [ordered]@{}
    foreach ($item in (Get-ChildItem Env:)) {
        if ($item.Name -notmatch $script:OutOfJobSecretNames) { $envMap[$item.Name] = $item.Value }
    }
    $spec = [ordered]@{ file = $FilePath; args = @($ArgumentList); cwd = $WorkingDirectory; env = $envMap }
    $spec | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $specPath -Encoding UTF8
    Set-Content -LiteralPath $wrapperPath -Value $script:OutOfJobWrapper -Encoding UTF8

    $taskName = 'hermes-e2e-out-of-job-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $action = New-ScheduledTaskAction -Execute $psExe -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $wrapperPath)
    $principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes $TimeoutMinutes)
    $result = $null
    try {
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        Write-Host "  out-of-job: started scheduled task $taskName (driver runs outside the runner step's job)"
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
            if (-not (Test-Path -LiteralPath $startedPath) -and $elapsed.TotalSeconds -gt $StartTimeoutSeconds) {
                Write-Host "  out-of-job: the task did not start within ${StartTimeoutSeconds}s (no interactive session for this user?); running the driver in-process"
                break
            }
            if ((Test-Path -LiteralPath $startedPath) -and ((Get-ScheduledTask -TaskName $taskName).State -eq 'Ready')) {
                # The wrapper ran and the task is idle without an exit file: it died.
                if ($null -eq $endedAt) { $endedAt = Get-Date }
                elseif (((Get-Date) - $endedAt).TotalSeconds -gt 10) { $result = 97; break }
            }
            if ($elapsed.TotalMinutes -gt ($TimeoutMinutes + 1)) { $result = 96; break }
        }
        if ($null -eq $result -and (Test-Path -LiteralPath $exitPath)) {
            $result = [int](Get-Content -LiteralPath $exitPath -Raw).Trim()
        }
    } finally {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        # The snapshot holds this step's environment.
        Remove-Item -LiteralPath $specPath -Force -ErrorAction SilentlyContinue
    }
    return $result
}
