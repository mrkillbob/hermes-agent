function Get-FleetDesktopMainProcess($Processes, [string]$DesktopExecutable) {
    # Require an executable-only main command too: orphaned Electron children share the executable.
    $mainCommands = @($DesktopExecutable, ('"' + $DesktopExecutable + '"'))
    $candidates = @($Processes | Where-Object {
        $_.ExecutablePath -eq $DesktopExecutable -and
        $_.Name -eq [IO.Path]::GetFileName($DesktopExecutable) -and
        $mainCommands -contains ([string]$_.CommandLine).Trim()
    })
    $candidateIds = @($candidates | ForEach-Object { $_.ProcessId })
    $candidates | Where-Object { $candidateIds -notcontains $_.ParentProcessId } |
        Select-Object -First 1
}

function Resolve-FleetHermesExecutable([string]$HermesRoot) {
    foreach ($name in @('hermes.exe', 'hermes.cmd')) {
        $candidate = Join-Path (Join-Path $HermesRoot 'bin') $name
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    throw 'No installed Hermes launcher exists in the configured bin directory'
}

function Test-FleetDiagnosticProcess([int]$ProcessId, [string]$StartUtc) {
    if (-not $ProcessId -or -not $StartUtc) { return $false }
    $process = $null
    try {
        $process = [Diagnostics.Process]::GetProcessById($ProcessId)
        return (-not $process.HasExited -and $process.StartTime.ToUniversalTime().ToString('o') -eq $StartUtc)
    } catch [ArgumentException] {
        return $false
    } catch [InvalidOperationException] {
        return $false
    } catch [ComponentModel.Win32Exception] {
        # Inaccessible process identity is not evidence that its logs are safe to delete.
        return $true
    } finally {
        if ($null -ne $process) { $process.Dispose() }
    }
}

function Remove-FleetRunnerDiagnosticHistory([string]$Root, [ValidateRange(1, 64)][int]$RetainCompleted = 8) {
    $completed = @()
    foreach ($directory in @(Get-ChildItem -LiteralPath $Root -Directory -Filter 'run-*')) {
        $path = Join-Path $directory.FullName 'process.json'
        if (-not (Test-Path -LiteralPath $path)) { continue }
        try { $meta = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json } catch {
            Write-Warning 'Unreadable fleet diagnostic receipt preserved'
            continue
        }
        if ($meta.outcome -eq 'running' -and
            -not (Test-FleetDiagnosticProcess $meta.owner_pid $meta.owner_start_utc) -and
            -not (Test-FleetDiagnosticProcess $meta.pid $meta.process_start_utc)) {
            $meta.outcome = 'unobserved-exit'
            $meta.ended_utc = [DateTime]::UtcNow.ToString('o')
            $meta | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding UTF8
        }
        if ($meta.ended_utc) {
            $completed += [pscustomobject]@{Directory=$directory.FullName;Ended=$meta.ended_utc}
        }
    }
    # Evict whole settled runs; never truncate output or remove live/unknown evidence.
    $completed | Sort-Object Ended -Descending | Select-Object -Skip $RetainCompleted |
        ForEach-Object { Remove-Item -LiteralPath $_.Directory -Recurse -Force }
}

function Start-FleetRunnerWithDiagnostics(
    [string]$Executable, [string]$Arguments, [string]$WorkingDirectory,
    [string]$Root, [ValidateRange(1, 64)][int]$RetainCompleted = 8
) {
    $directory = Join-Path $Root ('run-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $owner = [Diagnostics.Process]::GetCurrentProcess()
    $meta = [pscustomobject]@{
        schema_version=1; owner_pid=$owner.Id; owner_start_utc=$owner.StartTime.ToUniversalTime().ToString('o')
        pid=$null; process_start_utc=$null; started_utc=[DateTime]::UtcNow.ToString('o')
        ended_utc=$null; exit_code=$null; outcome='running'
    }
    $owner.Dispose()
    $receipt = Join-Path $directory 'process.json'
    $stdout = Join-Path $directory 'stdout.log'
    $stderr = Join-Path $directory 'stderr.log'
    try {
        $process = Start-Process -FilePath $Executable -ArgumentList $Arguments `
            -WorkingDirectory $WorkingDirectory -WindowStyle Hidden `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        # Keep the native process handle before a short-lived child disappears;
        # a later PID lookup cannot recover its exit code reliably.
        $null = $process.Handle
        $meta.pid = $process.Id
        $meta.process_start_utc = $process.StartTime.ToUniversalTime().ToString('o')
    } catch {
        $meta.outcome = 'launch-failed'
        $meta.ended_utc = [DateTime]::UtcNow.ToString('o')
        $meta | ConvertTo-Json | Set-Content -LiteralPath $receipt -Encoding UTF8
        Remove-FleetRunnerDiagnosticHistory $Root -RetainCompleted $RetainCompleted
        throw
    }
    $meta | ConvertTo-Json | Set-Content -LiteralPath $receipt -Encoding UTF8
    Remove-FleetRunnerDiagnosticHistory $Root -RetainCompleted $RetainCompleted
    [pscustomobject]@{Process=$process; Directory=$directory}
}

function Complete-FleetRunnerDiagnostics($Record, [string]$Root, [ValidateRange(1, 64)][int]$RetainCompleted = 8) {
    $Record.Process.Refresh()
    if (-not $Record.Process.HasExited) { throw 'Cannot settle diagnostics for a live fleet worker' }
    # Drain redirected output before recording completion or evicting any old run.
    $Record.Process.WaitForExit()
    $path = Join-Path $Record.Directory 'process.json'
    $meta = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    $meta.outcome = 'exited'
    $meta.exit_code = $Record.Process.ExitCode
    $meta.ended_utc = [DateTime]::UtcNow.ToString('o')
    $meta | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding UTF8
    Remove-FleetRunnerDiagnosticHistory $Root -RetainCompleted $RetainCompleted
}

function Stop-FleetRunnerWithDiagnostics($Record, [string]$Root) {
    try { $Record.Process.Kill() } catch {
        # Natural exit can win the race with Desktop shutdown. A failure to
        # stop a still-live process must remain visible to the supervisor.
        $Record.Process.Refresh()
        if (-not $Record.Process.HasExited) { throw }
    }
    $Record.Process.WaitForExit()
    Complete-FleetRunnerDiagnostics $Record $Root
}
