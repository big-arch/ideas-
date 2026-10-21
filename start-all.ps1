$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start-ai.ps1')
& (Join-Path $PSScriptRoot 'start-bot.ps1')
& (Join-Path $PSScriptRoot 'start-app.ps1')

& (Join-Path $PSScriptRoot 'start-bridge.ps1')
