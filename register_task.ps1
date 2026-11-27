$TaskName = "MonitorTrackerDK"
$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument '"C:\Users\hugod\.gemini\antigravity\scratch\monitor-tracker\run_silent.vbs"'
$Trigger = New-ScheduledTaskTrigger -Daily -At 09:00AM
$Trigger.EndBoundary = "2026-11-30T23:59:59"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)

# Unregister if already exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Seguimiento diario (09:00 CPH) de ofertas hasta Cyber Monday (30 nov 2026)."

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object TaskName, NextRunTime
