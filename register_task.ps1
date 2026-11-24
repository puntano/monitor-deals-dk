$TaskName = "MonitorTrackerDK"
$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument '"C:\Users\hugod\.gemini\antigravity\scratch\monitor-tracker\run_silent.vbs"'
$Trigger = New-ScheduledTaskTrigger -Daily -At 08:00AM
$Trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 08:00AM -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Hours 14)).Repetition
$Trigger.EndBoundary = "2027-01-31T23:59:59"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)

# Unregister if already exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Seguimiento cada 2h (08:00-22:00 CPH) de ofertas Black Friday de laptops Windows RTX 5070 Ti+ y monitores 32 4K OLED en Dinamarca."

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object TaskName, NextRunTime
