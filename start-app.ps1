param([switch]$Connect)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pidFile = Join-Path $projectRoot 'logs\app-process.json'
$runningApp = $null
if (Test-Path -LiteralPath $pidFile) {
    $savedApp = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $candidateApp = Get-Process -Id $savedApp.id -ErrorAction SilentlyContinue
    if ($candidateApp -and $candidateApp.StartTime.ToUniversalTime().Ticks -eq [long]$savedApp.startedUtcTicks) { $runningApp = $candidateApp }
}
if (-not $runningApp) {
    $runningApp = Start-Process -FilePath (Join-Path $projectRoot '.venv\Scripts\python.exe') -ArgumentList @('-m','garage_radar.webapp') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot 'logs\app.out.log') -RedirectStandardError (Join-Path $projectRoot 'logs\app.err.log') -PassThru
    @{id=$runningApp.Id;startedUtcTicks=$runningApp.StartTime.ToUniversalTime().Ticks} | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8
    Start-Sleep -Milliseconds 700
}
$appUrl = if ($Connect) { 'http://127.0.0.1:8766/?connect=1' } else { 'http://127.0.0.1:8766/' }
Start-Process $appUrl
Write-Output "Local application started, PID $($runningApp.Id)"
