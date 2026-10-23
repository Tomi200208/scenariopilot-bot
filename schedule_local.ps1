# Fallback when GitHub Actions cannot run: Windows scheduled tasks for the primary bot and the A/B control bot,
# every 30 minutes (MiniBench questions stay open about 3 hours). The machine must be on. Keys come from .env next
# to bot.py (METACULUS_TOKEN and METACULUS_TOKEN_CONTROL). Logs go to logs\. Remove with: .\schedule_local.ps1 -Remove
param([switch]$Remove)

$tasks = @{ "ScenarioPilotMetaculusBot" = "--mode tournament"; "ScenarioPilotMetaculusControl" = "--control" }
if ($Remove) {
    foreach ($name in $tasks.Keys) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {  # only "not registered" is silent
            Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
            "Removed $name"
        }
    }
    return
}
$dir = $PSScriptRoot
if (-not (Test-Path "$dir\.env")) { throw "Fill in $dir\.env first (copy .env.example)." }
New-Item -ItemType Directory -Force -Path "$dir\logs" | Out-Null
$py = "$dir\.venv\Scripts\python.exe"
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 25) -StartWhenAvailable
foreach ($name in $tasks.Keys) {
    $log = "$dir\logs\$name.log"
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"`"$py`" -X utf8 bot.py $($tasks[$name]) >> `"$log`" 2>&1`"" -WorkingDirectory $dir
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 30)
    # runs while the user is logged on (no elevated shell, full network access); missed runs start when available
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Description "Metaculus FutureEval bot" -Force -ErrorAction Stop | Out-Null
    "Registered ${name} (runs while you are logged on), log: $log"
}
