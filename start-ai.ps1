$ErrorActionPreference = 'Stop'
$runtimeRoot = Join-Path $PSScriptRoot 'runtime'
$ollamaPath = Join-Path $runtimeRoot 'ollama\ollama.exe'
$logDirectory = Join-Path $PSScriptRoot 'logs'
if (-not (Test-Path -LiteralPath $ollamaPath)) { throw 'Run setup-local-ai.py first.' }
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$env:OLLAMA_MODELS = Join-Path $runtimeRoot 'models'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_CONTEXT_LENGTH = '4096'
try {
    $version = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2
    Write-Output "Ollama already serving: $($version.version)"
    exit 0
} catch {
    $aiProcess = Start-Process -FilePath $ollamaPath -ArgumentList @('serve') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'ai.out.log') -RedirectStandardError (Join-Path $logDirectory 'ai.err.log') -PassThru
    @{ id = $aiProcess.Id; startedUtc = $aiProcess.StartTime.ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDirectory 'ai-process.json') -Encoding utf8
    Write-Output "Local Ollama started, PID $($aiProcess.Id)"
}
