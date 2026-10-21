$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'logs\bridge-process.json'
if (Test-Path -LiteralPath $pidFile) {
    $savedBridge = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $candidateBridge = Get-Process -Id $savedBridge.id -ErrorAction SilentlyContinue
    if ($candidateBridge -and $candidateBridge.StartTime.ToUniversalTime().Ticks -eq [long]$savedBridge.startedUtcTicks) { Stop-Process -Id $candidateBridge.Id; Write-Output 'Cloud bridge stopped' }
}
