function Get-FleetDesktopMainProcess($Processes, [string]$DesktopExecutable) {
    # Electron children share the executable; their parent is another candidate.
    $candidates = @($Processes | Where-Object {
        $_.ExecutablePath -eq $DesktopExecutable -and
        $_.Name -eq [IO.Path]::GetFileName($DesktopExecutable)
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
