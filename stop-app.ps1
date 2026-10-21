$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'logs\app-process.json'
if (Test-Path -LiteralPath $pidFile) {
    $savedApp = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $candidateApp = Get-Process -Id $savedApp.id -ErrorAction SilentlyContinue
    if ($candidateApp -and $candidateApp.StartTime.ToUniversalTime().Ticks -eq [long]$savedApp.startedUtcTicks) {
        Stop-Process -Id $candidateApp.Id
        Write-Output "Stopped project application PID $($candidateApp.Id)"
    }
}
