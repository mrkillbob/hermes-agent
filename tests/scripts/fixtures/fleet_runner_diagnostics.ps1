param([string]$Helper, [string]$Root, [string]$Mode)
$ErrorActionPreference='Stop'
. $Helper
function Assert-True($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Encode-Child([string]$Code) { '-NoProfile -NonInteractive -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Code)) }
$exe=(Get-Process -Id $PID).Path
$records=@()
$active=$null
$gate=$null
try {
 if ($Mode -eq 'retention') {
  $gateName='Local\HermesFleetDiagnosticTest-' + [Guid]::NewGuid().ToString('N')
  $gate=New-Object Threading.EventWaitHandle($false, [Threading.EventResetMode]::ManualReset, $gateName)
  $active=Start-FleetRunnerWithDiagnostics $exe (Encode-Child "`$e=[Threading.EventWaitHandle]::OpenExisting('$gateName'); `$e.WaitOne(30000) | Out-Null") $Root $Root -RetainCompleted 2
  foreach($i in 1..4) {
   $args=Encode-Child "[Console]::Out.WriteLine('stdout-$i'); [Console]::Out.Write('tail-$i'); [Console]::Error.Write('stderr-$i'); exit $i"
   $record=Start-FleetRunnerWithDiagnostics $exe $args $Root $Root -RetainCompleted 2
   Assert-True ($record.Process.WaitForExit(30000)) 'inert child did not exit'
   Complete-FleetRunnerDiagnostics $record $Root -RetainCompleted 2
   $records+=@($record)
  }
  $retained=@(Get-ChildItem -LiteralPath $Root -Directory -Filter 'run-*')
  Assert-True ($retained.Count -eq 3) 'must retain two completed runs plus active run'
  Assert-True (Test-Path -LiteralPath $active.Directory) 'active evidence pruned'
  Assert-True (-not $active.Process.HasExited) 'active child lost'
  foreach($i in 3..4) {
   $dir=$records[$i-1].Directory
   $meta=Get-Content -LiteralPath (Join-Path $dir 'process.json') -Raw | ConvertFrom-Json
   Assert-True ($meta.pid -eq $records[$i-1].Process.Id -and $meta.exit_code -eq $i -and $meta.ended_utc) 'missing process exit receipt'
   Assert-True ([IO.File]::ReadAllText((Join-Path $dir 'stdout.log')).Contains("tail-$i")) 'lost final stdout'
   Assert-True ([IO.File]::ReadAllText((Join-Path $dir 'stderr.log')).Contains("stderr-$i")) 'lost stderr'
  }
  Assert-True (-not (Test-Path -LiteralPath $records[0].Directory)) 'old completed history unbounded'
 } elseif ($Mode -eq 'stop-race') {
  $record=Start-FleetRunnerWithDiagnostics $exe (Encode-Child "[Console]::Error.Write('natural-exit'); exit 7") $Root $Root
  Assert-True ($record.Process.WaitForExit(30000)) 'inert child did not exit'
  # The process exits between the supervisor's liveness check and its stop call.
  Stop-FleetRunnerWithDiagnostics $record $Root
  $meta=Get-Content -LiteralPath (Join-Path $record.Directory 'process.json') -Raw | ConvertFrom-Json
  Assert-True ($meta.outcome -eq 'exited' -and $meta.exit_code -eq 7) 'concurrent natural exit not settled'
  Assert-True ([IO.File]::ReadAllText((Join-Path $record.Directory 'stderr.log')).Contains('natural-exit')) 'shutdown output lost'
 } else {
  # A real exited process supplies the identity of a supervisor lost before settlement.
  $orphan=Start-FleetRunnerWithDiagnostics $exe (Encode-Child "[Console]::Error.Write('orphan-output'); exit 9") $Root $Root -RetainCompleted 2
  Assert-True ($orphan.Process.WaitForExit(30000)) 'orphan child did not exit'
  $path=Join-Path $orphan.Directory 'process.json'
  $meta=Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
  $meta.owner_pid=$meta.pid; $meta.owner_start_utc=$meta.process_start_utc
  $meta | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding UTF8
  Remove-FleetRunnerDiagnosticHistory $Root -RetainCompleted 2
  $meta=Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
  Assert-True ($meta.outcome -eq 'unobserved-exit' -and $null -eq $meta.exit_code) 'orphan misreported as successful'
  Assert-True ([IO.File]::ReadAllText((Join-Path $orphan.Directory 'stderr.log')).Contains('orphan-output')) 'orphan output lost'
  foreach($i in 1..3) {
   $failed=$false
   try { Start-FleetRunnerWithDiagnostics (Join-Path $Root 'missing.exe') '' $Root $Root -RetainCompleted 2 | Out-Null } catch { $failed=$true }
   Assert-True $failed 'launch failure hidden'
  }
  $retained=@(Get-ChildItem -LiteralPath $Root -Directory -Filter 'run-*')
  Assert-True ($retained.Count -eq 2) 'failed launches unbounded'
  foreach($dir in $retained) {
   $meta=Get-Content -LiteralPath (Join-Path $dir.FullName 'process.json') -Raw | ConvertFrom-Json
   Assert-True ($meta.outcome -eq 'launch-failed' -and $null -eq $meta.exit_code -and $meta.ended_utc) 'launch failure receipt missing'
  }
 }
 @{mode=$Mode;passed=$true} | ConvertTo-Json -Compress
} finally {
 if ($null -ne $gate) { $gate.Set() | Out-Null }
 if ($null -ne $active) {
  if (-not $active.Process.WaitForExit(30000)) { $active.Process.Kill(); $active.Process.WaitForExit() }
  Complete-FleetRunnerDiagnostics $active $Root -RetainCompleted 2
 }
 if ($null -ne $gate) { $gate.Dispose() }
}
