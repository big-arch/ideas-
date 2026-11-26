$ErrorActionPreference = 'Stop'
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskName = 'Garage Radar - local services'
$scriptPath = Join-Path $PSScriptRoot 'maintain-radar.ps1'
$action = New-ScheduledTaskAction -Execute (Get-Command pwsh.exe).Source -WorkingDirectory $PSScriptRoot -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}"' -f $scriptPath)
$login = New-ScheduledTaskTrigger -AtLogOn -User $userId
$periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($login, $periodic) -Principal $principal -Settings $settings -Description 'Restores the local Telegram bot, Mini App and bridge.' -Force | Select-Object TaskName,State
