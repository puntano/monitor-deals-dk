$TaskName = "MonitorTrackerDK"
$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument '"C:\Users\hugod\.gemini\antigravity\scratch\monitor-tracker\run_silent.vbs"'
$Trigger = New-ScheduledTaskTrigger -Daily -At 09:00AM
# Set end boundary to 2026-12-01T23:59:59
$Trigger.EndBoundary = "2026-12-01T23:59:59"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)

# Unregister if already exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Seguimiento diario de ofertas de monitores 32 pulgadas 4K en Dinamarca a las 09:00 Europe/Copenhagen hasta el 1 de diciembre de 2026."

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object TaskName, NextRunTime
