$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'logs\bridge-process.json'
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'runtime\cloud-bridge.json'))) { Write-Output 'Cloud bridge is not configured'; exit 0 }
if (Test-Path -LiteralPath $pidFile) {
    $savedBridge = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $candidateBridge = Get-Process -Id $savedBridge.id -ErrorAction SilentlyContinue
    if ($candidateBridge -and $candidateBridge.StartTime.ToUniversalTime().Ticks -eq [long]$savedBridge.startedUtcTicks) { Write-Output "Bridge already running, PID $($candidateBridge.Id)"; exit 0 }
}
$bridgeProcess = Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -ArgumentList @('-m','garage_radar.cloud_bridge') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'logs\bridge.out.log') -RedirectStandardError (Join-Path $PSScriptRoot 'logs\bridge.err.log') -PassThru
@{id=$bridgeProcess.Id;startedUtcTicks=$bridgeProcess.StartTime.ToUniversalTime().Ticks} | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8
Write-Output "Cloud bridge started, PID $($bridgeProcess.Id)"
