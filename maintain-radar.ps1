# Used by the current user's scheduled task, including after Windows sign-in.
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot 'logs') -Force | Out-Null
& (Join-Path $PSScriptRoot 'start-app.ps1') -NoBrowser
& (Join-Path $PSScriptRoot 'start-bot.ps1')
& (Join-Path $PSScriptRoot 'start-bridge.ps1')
