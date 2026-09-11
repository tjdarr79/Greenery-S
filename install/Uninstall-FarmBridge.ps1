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

Write-Host "  Stopping the bridge..." -ForegroundColor Cyan
try { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue } catch { }

Get-Process -Name "python", "py" -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
        if ($cmdline -and $cmdline -like "*farm_bridge.py*") {
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            Write-Host "    Stopped process $($_.Id)" -ForegroundColor Gray
        }
    } catch { }
}

Write-Host "  Removing the startup task..." -ForegroundColor Cyan
try {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
    Write-Host "    OK   Startup task removed" -ForegroundColor Green
} catch {
    Write-Host "    NOTE No startup task found - nothing to remove" -ForegroundColor Yellow
}

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
