#Requires -Version 5.1
<#
    Removes the Greenery S Farm Bridge startup task.

    Deliberately conservative: it stops and removes the scheduled task, and
    stops the running process. It does NOT delete C:\Farm, your settings, or
    your logs unless you explicitly ask - an uninstaller that silently destroys
    a working MQTT config is a bad trade for the two seconds it saves.
#>

[CmdletBinding()]
param([string]$InstallDir = "C:\Farm")

$ErrorActionPreference = "Continue"
$taskName = "Greenery S Farm Bridge"

Clear-Host
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor White
Write-Host "   GREENERY S FARM BRIDGE - Uninstall" -ForegroundColor White
Write-Host "  ============================================================" -ForegroundColor White
Write-Host ""

# Left running beside the Home Assistant app, this bridge flooded a farm's
# database (CHANGELOG 1.5.1). So remove every task that starts it: the one
# INSTALL-FARM-BRIDGE.bat registers, "FarmBridge" from WINDOWS-INSTALL.md, and
# any other whose action runs farm_bridge.py.
$knownNames = @($taskName, "FarmBridge")

function Get-BridgeTasks {
    @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
        $task = $_
        $actions = ($task.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " "
        ($knownNames -contains $task.TaskName) -or ($actions -match "farm_bridge|run-bridge\.cmd")
    })
}

# By command line, not process name: python.exe, pythonw.exe and py.exe all
# run it. Also matches a renamed farm_bridge.py.retired.
function Get-BridgeProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and $_.CommandLine -like "*farm_bridge.py*" -and $_.ProcessId -ne $PID
    })
}

$removed = @()
$stopped = @()

# Tasks first: each restarts the bridge a minute after it stops.
Write-Host "  Removing the startup task..." -ForegroundColor Cyan
$tasks = Get-BridgeTasks
foreach ($t in $tasks) {
    $label = $t.TaskPath + $t.TaskName
    try { Stop-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -ErrorAction SilentlyContinue } catch { }
    try {
        Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Confirm:$false -ErrorAction Stop
        $removed += $label
        Write-Host "    OK   Removed task $label" -ForegroundColor Green
    } catch {
        Write-Host "    FAIL Could not remove task ${label}: $($_.Exception.Message)" -ForegroundColor Red
    }
}
if ($tasks.Count -eq 0) {
    Write-Host "    NOTE No farm bridge task found - nothing to remove" -ForegroundColor Yellow
}

Write-Host "  Stopping the bridge..." -ForegroundColor Cyan
$procs = Get-BridgeProcesses
foreach ($p in $procs) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        $stopped += "$($p.Name) (process $($p.ProcessId))"
        Write-Host "    OK   Stopped $($p.Name) (process $($p.ProcessId))" -ForegroundColor Green
    } catch {
        Write-Host "    FAIL Could not stop process $($p.ProcessId): $($_.Exception.Message)" -ForegroundColor Red
    }
}
if ($procs.Count -eq 0) {
    Write-Host "    NOTE No farm_bridge.py process running" -ForegroundColor Yellow
}

Start-Sleep -Seconds 2
$leftTasks = Get-BridgeTasks
$leftProcs = Get-BridgeProcesses

Write-Host ""
Write-Host "  ------------------------------------------------------------" -ForegroundColor White
Write-Host "   What this did" -ForegroundColor White
Write-Host "  ------------------------------------------------------------" -ForegroundColor White
Write-Host ("    Tasks removed:     {0}" -f $removed.Count) -ForegroundColor White
$removed | ForEach-Object { Write-Host "      $_" -ForegroundColor Gray }
Write-Host ("    Bridges stopped:   {0}" -f $stopped.Count) -ForegroundColor White
$stopped | ForEach-Object { Write-Host "      $_" -ForegroundColor Gray }
if ($leftTasks.Count -or $leftProcs.Count) {
    Write-Host ""
    Write-Host "    STILL PRESENT - remove by hand:" -ForegroundColor Red
    $leftTasks | ForEach-Object { Write-Host "      task    $($_.TaskPath)$($_.TaskName)" -ForegroundColor Red }
    $leftProcs | ForEach-Object { Write-Host "      process $($_.ProcessId) $($_.CommandLine)" -ForegroundColor Red }
} else {
    Write-Host "    No farm bridge task or process is left on this PC." -ForegroundColor Green
}
Write-Host ""
Write-Host "  Then check Task Scheduler yourself (Start > Task Scheduler >" -ForegroundColor White
Write-Host "  Task Scheduler Library) for any other task that starts the" -ForegroundColor White
Write-Host "  bridge, e.g. one created under another name." -ForegroundColor White

Write-Host ""
Write-Host "  The bridge will no longer start with Windows." -ForegroundColor White
Write-Host ""
Write-Host "  Your files in $InstallDir were KEPT, including:" -ForegroundColor Gray
Write-Host "    farm-bridge.env   your Home Assistant settings" -ForegroundColor Gray
Write-Host "    bridge.log        the run history" -ForegroundColor Gray
Write-Host ""
Write-Host "  Re-running INSTALL-FARM-BRIDGE.bat will pick those settings" -ForegroundColor Gray
Write-Host "  back up, so reinstalling takes seconds." -ForegroundColor Gray
Write-Host ""

$answer = Read-Host "  Delete $InstallDir and everything in it as well? (y/N)"
if ($answer -match "^[Yy]") {
    Write-Host ""
    Write-Host "  This permanently deletes your MQTT settings and logs." -ForegroundColor Yellow
    $confirm = Read-Host "  Type DELETE to confirm"
    if ($confirm -ceq "DELETE") {
        try {
            Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction Stop
            Write-Host "    OK   $InstallDir deleted" -ForegroundColor Green
        } catch {
            Write-Host "    Could not delete: $($_.Exception.Message)" -ForegroundColor Red
        }
    } else {
        Write-Host "    Not deleted - you did not type DELETE." -ForegroundColor Gray
    }
} else {
    Write-Host "    Files kept." -ForegroundColor Gray
}

Write-Host ""
Write-Host "  Note: Python and its packages were left installed. Remove them" -ForegroundColor Gray
Write-Host "  through Settings > Apps if you want them gone." -ForegroundColor Gray
Write-Host ""
