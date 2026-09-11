#Requires -Version 5.1
<#
    Greenery S Farm Bridge - Windows Installer

    Launched by INSTALL-FARM-BRIDGE.bat (which handles elevation).

    Written for Windows PowerShell 5.1, which is what ships with Windows -
    so no ternaries, no null-coalescing, nothing 7.x-only.

    Every step reports plainly and stops on real failure rather than
    half-installing. A transcript is written to C:\Farm\install-log.txt so a
    failed run can be diagnosed without reproducing it.
#>

[CmdletBinding()]
param(
    [string]$InstallDir = "C:\Farm",
    [string]$FarmHost   = "192.168.200.200",
    [int]   $FarmPort   = 3001
)

$ErrorActionPreference = "Stop"
$script:StepNumber = 0

# Pinned fallback. winget is tried first and self-updates; this is only used
# when winget is absent or fails. Bump when it ages.
$PythonFallbackUrl = "https://www.python.org/ftp/python/3.14.7/python-3.14.7-amd64.exe"
$MinPythonMajor = 3
$MinPythonMinor = 9

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

function Write-Step {
    param([string]$Message)
    $script:StepNumber++
    Write-Host ""
    Write-Host ("[{0}] {1}" -f $script:StepNumber, $Message) -ForegroundColor Cyan
}
function Write-Ok   { param([string]$m) Write-Host "    OK   $m" -ForegroundColor Green }
function Write-Info { param([string]$m) Write-Host "         $m" -ForegroundColor Gray }
function Write-Warn2{ param([string]$m) Write-Host "    NOTE $m" -ForegroundColor Yellow }

function Stop-Install {
    param([string]$Problem, [string]$WhatToDo)
    Write-Host ""
    Write-Host "  ------------------------------------------------------------" -ForegroundColor Red
    Write-Host "   INSTALL STOPPED" -ForegroundColor Red
    Write-Host "  ------------------------------------------------------------" -ForegroundColor Red
    Write-Host ""
    Write-Host "   Problem:  $Problem" -ForegroundColor Red
    Write-Host ""
    Write-Host "   Do this:  $WhatToDo" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   Nothing was left half-configured. Fix the above and run" -ForegroundColor Gray
    Write-Host "   INSTALL-FARM-BRIDGE.bat again - it is safe to re-run." -ForegroundColor Gray
    Write-Host ""
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

function Test-TcpPort {
    param([string]$ComputerName, [int]$Port, [int]$TimeoutMs = 4000)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($ComputerName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

# ---------------------------------------------------------------------------

Clear-Host
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor White
Write-Host "   GREENERY S -> HOME ASSISTANT BRIDGE" -ForegroundColor White
Write-Host "   Windows Installer" -ForegroundColor White
Write-Host "  ============================================================" -ForegroundColor White
Write-Host ""
Write-Host "   This will:" -ForegroundColor Gray
Write-Host "     - install Python if it is not already here" -ForegroundColor Gray
Write-Host "     - copy the bridge into $InstallDir" -ForegroundColor Gray
Write-Host "     - ask you for your Home Assistant MQTT details" -ForegroundColor Gray
Write-Host "     - test that it can reach the farm and the broker" -ForegroundColor Gray
Write-Host "     - set it to start automatically with Windows" -ForegroundColor Gray
Write-Host ""
Write-Host "   Takes about 5 minutes. Safe to run again if anything fails." -ForegroundColor Gray
Write-Host ""

$SourceDir = Split-Path -Parent $PSScriptRoot

# --- Step 1: sanity -------------------------------------------------------
Write-Step "Checking the installer files"

$required = @("farm_bridge.py", "requirements.txt", "farm-bridge.env.example")
$missing = @()
foreach ($f in $required) {
    if (-not (Test-Path (Join-Path $SourceDir $f))) { $missing += $f }
}
if ($missing.Count -gt 0) {
    Stop-Install -Problem ("These files are missing from " + $SourceDir + ": " + ($missing -join ", ")) `
                 -WhatToDo "Extract the whole downloaded folder before running the installer. Do not run it from inside the ZIP."
}
Write-Ok "Found the bridge files"

if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
try { Start-Transcript -Path (Join-Path $InstallDir "install-log.txt") -Append | Out-Null } catch { }

# --- Step 2: Python -------------------------------------------------------
Write-Step "Checking for Python"

function Find-Python {
    $candidates = @()
    $py = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($py) { $candidates += @{ Exe = $py.Source; Args = @("-3") } }
    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($python) { $candidates += @{ Exe = $python.Source; Args = @() } }
    foreach ($guess in @(
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe",
        "C:\Program Files\Python314\python.exe",
        "C:\Program Files\Python313\python.exe",
        "C:\Program Files\Python312\python.exe"
    )) {
        if (Test-Path $guess) { $candidates += @{ Exe = $guess; Args = @() } }
    }

    foreach ($c in $candidates) {
        try {
            $verArgs = $c.Args + @("-c", "import sys;print('%d.%d' % sys.version_info[:2])")
            $out = & $c.Exe $verArgs 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $out) { continue }
            $parts = ("$out".Trim()) -split "\."
            if ([int]$parts[0] -gt $MinPythonMajor -or
                ([int]$parts[0] -eq $MinPythonMajor -and [int]$parts[1] -ge $MinPythonMinor)) {
                return @{ Exe = $c.Exe; Args = $c.Args; Version = "$out".Trim() }
            }
        } catch { continue }
    }
    return $null
}

$python = Find-Python

if ($python) {
    Write-Ok ("Python " + $python.Version + " already installed")
} else {
    Write-Info "Not found. Installing it now - this takes a few minutes."

    $installed = $false
    if (Get-Command "winget.exe" -ErrorAction SilentlyContinue) {
        Write-Info "Trying winget..."
        foreach ($id in @("Python.Python.3.14", "Python.Python.3.13", "Python.Python.3.12")) {
            try {
                & winget.exe install --id $id -e --source winget `
                    --accept-package-agreements --accept-source-agreements --silent 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { $installed = $true; Write-Ok "Installed via winget ($id)"; break }
            } catch { }
        }
    }

    if (-not $installed) {
        Write-Info "Downloading the installer from python.org..."
        $exe = Join-Path $env:TEMP "python-farmbridge-setup.exe"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $PythonFallbackUrl -OutFile $exe -UseBasicParsing
        } catch {
            Stop-Install -Problem "Could not download Python. This PC may have no internet access right now." `
                         -WhatToDo "Check the internet connection and run the installer again. If this PC is intentionally offline, install Python manually from python.org on a USB stick, then re-run."
        }
        Write-Info "Installing Python (silent, no clicking needed)..."
        $p = Start-Process -FilePath $exe -Wait -PassThru -ArgumentList @(
            "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_pip=1", "Include_test=0"
        )
        if ($p.ExitCode -ne 0) {
            Stop-Install -Problem ("The Python installer failed with code " + $p.ExitCode) `
                         -WhatToDo "Install Python yourself from python.org - tick 'Add python.exe to PATH' on the first screen - then run this installer again."
        }
        Remove-Item $exe -Force -ErrorAction SilentlyContinue
        Write-Ok "Python installed"
    }

    # PATH in this process is stale after an install; refresh it.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    $python = Find-Python
    if (-not $python) {
        Stop-Install -Problem "Python installed but Windows still cannot find it." `
                     -WhatToDo "Restart the computer, then run INSTALL-FARM-BRIDGE.bat again. This usually clears it."
    }
    Write-Ok ("Python " + $python.Version + " ready")
}

$PythonExe = $python.Exe
$PythonArgs = $python.Args

# --- Step 3: copy files ---------------------------------------------------
Write-Step "Copying the bridge into $InstallDir"

# Stop any previous copy first. Copying over a running script risks a file
# lock, and would otherwise leave the OLD code running until the next reboot -
# an upgrade that silently does nothing is worse than one that fails loudly.
try {
    $existing = Get-ScheduledTask -TaskName "Greenery S Farm Bridge" -ErrorAction SilentlyContinue
    if ($existing) {
        Stop-ScheduledTask -TaskName "Greenery S Farm Bridge" -ErrorAction SilentlyContinue
        Get-Process -Name "python", "py" -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path -like "*python*" } |
            ForEach-Object {
                try {
                    $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
                    if ($cmdline -and $cmdline -like "*farm_bridge.py*") {
                        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
                    }
                } catch { }
            }
        Start-Sleep -Seconds 2
        Write-Info "Stopped the previous bridge so it can be updated"
    }
} catch { }

foreach ($f in @("farm_bridge.py", "requirements.txt", "farm-bridge.env.example")) {
    Copy-Item (Join-Path $SourceDir $f) -Destination $InstallDir -Force
}
$toolsSrc = Join-Path $SourceDir "tools"
if (Test-Path $toolsSrc) {
    Copy-Item $toolsSrc -Destination $InstallDir -Recurse -Force
}
Write-Ok "Files copied"

# --- Step 4: dependencies -------------------------------------------------
Write-Step "Installing the Python packages the bridge needs"

$pipArgs = $PythonArgs + @("-m", "pip", "install", "--upgrade", "pip")
& $PythonExe $pipArgs 2>&1 | Out-Null

$pipArgs = $PythonArgs + @("-m", "pip", "install", "-r", (Join-Path $InstallDir "requirements.txt"))
& $PythonExe $pipArgs 2>&1 | ForEach-Object { Write-Info $_ }
if ($LASTEXITCODE -ne 0) {
    Stop-Install -Problem "Could not install the Python packages (aiohttp, paho-mqtt, python-dotenv)." `
                 -WhatToDo "Usually a temporary internet problem. Run the installer again. If it keeps failing, check whether a firewall is blocking pypi.org."
}
Write-Ok "Packages installed"

# --- Step 5: settings -----------------------------------------------------
Write-Step "Your Home Assistant settings"

$envFile = Join-Path $InstallDir "farm-bridge.env"
if (Test-Path $envFile) {
    Write-Warn2 "Settings already exist at $envFile"
    $reuse = Read-Host "    Keep the existing settings? (Y/n)"
    if ($reuse -eq "" -or $reuse -match "^[Yy]") {
        Write-Ok "Keeping existing settings"
        $skipConfig = $true
    }
}

if (-not $skipConfig) {
    Write-Host ""
    Write-Host "    Three questions. Find these in Home Assistant under" -ForegroundColor Gray
    Write-Host "    Settings > Add-ons > Mosquitto broker." -ForegroundColor Gray
    Write-Host ""
    Write-Host "    IMPORTANT: use the IP ADDRESS of Home Assistant, not" -ForegroundColor Yellow
    Write-Host "    'homeassistant.local'. The name does not always resolve" -ForegroundColor Yellow
    Write-Host "    and is the most common cause of this not working." -ForegroundColor Yellow
    Write-Host ""

    $mqttHost = ""
    while ($mqttHost -eq "") {
        $mqttHost = (Read-Host "    Home Assistant IP address (e.g. 192.168.200.187)").Trim()
        if ($mqttHost -match "homeassistant") {
            Write-Warn2 "Please use the numeric IP address instead."
            $mqttHost = ""
        }
    }

    $mqttUser = (Read-Host "    MQTT username").Trim()

    Write-Host "    MQTT password (typing is hidden)" -ForegroundColor Gray
    $securePass = Read-Host "    Password" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePass)
    $mqttPass = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

    $lines = @(
        "# Greenery S Farm Bridge settings",
        "# Written by the installer on $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
        "",
        "FARM_SSE_URL=http://${FarmHost}:${FarmPort}/farm-data",
        "",
        "FARM_MQTT_HOST=$mqttHost",
        "FARM_MQTT_USER=$mqttUser",
        "FARM_MQTT_PASS=$mqttPass"
    )
    Set-Content -Path $envFile -Value $lines -Encoding ASCII

    # This file holds a password. Lock it to Administrators and SYSTEM.
    try {
        $acl = Get-Acl $envFile
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($who in @("BUILTIN\Administrators", "NT AUTHORITY\SYSTEM")) {
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                $who, "FullControl", "Allow")
            $acl.AddAccessRule($rule)
        }
        Set-Acl -Path $envFile -AclObject $acl
        Write-Ok "Settings saved and locked down"
    } catch {
        Write-Warn2 "Settings saved, but could not restrict file permissions."
    }
}

# --- Step 6: connectivity -------------------------------------------------
Write-Step "Testing the connections"

$envText = Get-Content $envFile -Raw
$mqttHostLine = ([regex]::Match($envText, "FARM_MQTT_HOST=(.+)")).Groups[1].Value.Trim()

if (Test-TcpPort -ComputerName $FarmHost -Port $FarmPort) {
    Write-Ok "Reached the farm at ${FarmHost}:${FarmPort}"
} else {
    Write-Warn2 "Could NOT reach the farm at ${FarmHost}:${FarmPort}"
    Write-Info "The bridge will keep retrying, but check that this PC is on the"
    Write-Info "same network as the farm and that the hub controller is powered."
}

if ($mqttHostLine -and (Test-TcpPort -ComputerName $mqttHostLine -Port 1883)) {
    Write-Ok "Reached the MQTT broker at ${mqttHostLine}:1883"
} else {
    Write-Warn2 "Could NOT reach the MQTT broker at ${mqttHostLine}:1883"
    Write-Info "Check that the Mosquitto add-on is installed AND STARTED in"
    Write-Info "Home Assistant, and that the IP address above is correct."
}

# --- Step 7: runner + startup task ---------------------------------------
Write-Step "Setting it to start automatically with Windows"

$runCmd = Join-Path $InstallDir "run-bridge.cmd"

# The executable must be quoted, but its arguments must NOT be inside those
# quotes - otherwise cmd looks for a program literally named "py.exe -3".
$exeQuoted = '"' + $PythonExe + '"'
$argsPart  = ""
if ($PythonArgs.Count -gt 0) { $argsPart = " " + ($PythonArgs -join " ") }

$runLines = @(
    "@echo off",
    "REM Started by the 'Greenery S Farm Bridge' scheduled task.",
    "cd /d `"$InstallDir`"",
    "if exist bridge.log for %%A in (bridge.log) do if %%~zA GTR 10485760 move /y bridge.log bridge.log.old >nul",
    "$exeQuoted$argsPart farm_bridge.py >> bridge.log 2>&1"
)
Set-Content -Path $runCmd -Value $runLines -Encoding ASCII

$taskName = "Greenery S Farm Bridge"
try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue } catch { }

try {
    $action    = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$runCmd`"" -WorkingDirectory $InstallDir
    $trigger   = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable

    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description "Publishes Greenery S farm sensors to Home Assistant over MQTT." | Out-Null
    Write-Ok "Startup task created"
} catch {
    Stop-Install -Problem ("Could not create the startup task: " + $_.Exception.Message) `
                 -WhatToDo "Make sure you launched INSTALL-FARM-BRIDGE.bat (which asks for Administrator), not the .ps1 directly."
}

# --- Step 8: start and verify --------------------------------------------
Write-Step "Starting the bridge"

Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 12

$logFile = Join-Path $InstallDir "bridge.log"
$running = $false
if (Test-Path $logFile) {
    $log = Get-Content $logFile -Tail 40 -ErrorAction SilentlyContinue
    if ($log -match "MQTT connected") { $running = $true }
}

if ($running) {
    Write-Ok "Bridge is running and connected to Home Assistant"
} else {
    Write-Warn2 "The bridge started but has not confirmed an MQTT connection yet."
    Write-Info "This is often just slow startup. Check again in a minute:"
    Write-Info "  notepad $logFile"
    if (Test-Path $logFile) {
        Write-Host ""
        Write-Host "    --- last few log lines ---" -ForegroundColor DarkGray
        Get-Content $logFile -Tail 12 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    }
}

# --- Done -----------------------------------------------------------------
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host "   INSTALL COMPLETE" -ForegroundColor Green
Write-Host "  ============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "   The bridge is installed in $InstallDir and will start" -ForegroundColor White
Write-Host "   automatically every time this PC boots." -ForegroundColor White
Write-Host ""
Write-Host "   NOW DO THIS IN HOME ASSISTANT:" -ForegroundColor Yellow
Write-Host ""
Write-Host "   1. Settings > Devices & Services > MQTT > Greenery S Farm" -ForegroundColor White
Write-Host "      You should see about 54 sensors. If you do, the hard part" -ForegroundColor Gray
Write-Host "      is done." -ForegroundColor Gray
Write-Host ""
Write-Host "   2. Set up alerts - open README.md and follow" -ForegroundColor White
Write-Host "      'New install - do these in order', steps 3 onward." -ForegroundColor Gray
Write-Host "      Step 3 is the notification test. DO NOT SKIP IT." -ForegroundColor Yellow
Write-Host ""
Write-Host "   Log file:   $logFile" -ForegroundColor Gray
Write-Host "   Settings:   $envFile" -ForegroundColor Gray
Write-Host "   To remove:  UNINSTALL-FARM-BRIDGE.bat" -ForegroundColor Gray
Write-Host ""

try { Stop-Transcript | Out-Null } catch { }
