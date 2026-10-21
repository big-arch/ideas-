$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'logs\bot-process.json'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'No project bot process recorded.'; exit 0 }
$savedProcess = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
$botProcess = Get-Process -Id $savedProcess.id -ErrorAction SilentlyContinue
if (-not $botProcess) { Write-Output 'Bot already stopped.'; exit 0 }
$savedStart = if ($savedProcess.startedUtcTicks) { [long]$savedProcess.startedUtcTicks } else { ([datetime]$savedProcess.startedUtc).ToUniversalTime().Ticks }
if ($botProcess.StartTime.ToUniversalTime().Ticks -ne $savedStart) { throw 'PID reused by another process; refusing to stop it.' }
Stop-Process -Id $botProcess.Id
Write-Output "Stopped project bot PID $($botProcess.Id)"
