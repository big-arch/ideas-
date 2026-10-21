$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$logDirectory = Join-Path $projectRoot 'logs'
$pidFile = Join-Path $logDirectory 'bot-process.json'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create the project .venv first.' }
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
if (Test-Path -LiteralPath $pidFile) {
    $savedProcess = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $existingProcess = Get-Process -Id $savedProcess.id -ErrorAction SilentlyContinue
    $savedStart = if ($savedProcess.startedUtcTicks) { [long]$savedProcess.startedUtcTicks } else { ([datetime]$savedProcess.startedUtc).ToUniversalTime().Ticks }
    if ($existingProcess -and $existingProcess.StartTime.ToUniversalTime().Ticks -eq $savedStart) {
        Write-Output "Bot already running, PID $($existingProcess.Id)"
        exit 0
    }
}
$botProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'garage_radar', 'bot') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'bot.out.log') -RedirectStandardError (Join-Path $logDirectory 'bot.err.log') -PassThru
@{ id = $botProcess.Id; startedUtcTicks = $botProcess.StartTime.ToUniversalTime().Ticks } | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8
Write-Output "Bot started, PID $($botProcess.Id)"
