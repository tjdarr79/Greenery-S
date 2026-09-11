# install/

One-click Windows installer for the farm bridge. Built so a non-technical
operator can get from a downloaded ZIP to a running bridge without touching a
command line.

## For the operator

Extract the ZIP, then **double-click `INSTALL-FARM-BRIDGE.bat`** in the main
folder. Click **Yes** on the Windows permission prompt. That is the whole
instruction.

To remove it later, double-click `UNINSTALL-FARM-BRIDGE.bat`.

## What the installer does

| Step | |
|---|---|
| 1 | Checks the downloaded files are all present |
| 2 | Finds Python, or installs it silently (winget first, python.org fallback) |
| 3 | Stops any previous bridge, then copies the files into `C:\Farm` |
| 4 | Installs aiohttp, paho-mqtt, python-dotenv |
| 5 | Asks for the Home Assistant IP, MQTT username and password |
| 6 | Tests it can actually reach the farm and the broker |
| 7 | Creates a Windows startup task that survives reboots |
| 8 | Starts it and confirms MQTT connected in the log |

Transcript of every run: `C:\Farm\install-log.txt`.

## Design decisions worth knowing

**Safe to re-run.** Every step is idempotent. It offers to keep existing
settings, and doubles as the upgrade path — drop in a new `farm_bridge.py`,
re-run, done.

**Stops the old bridge before copying.** Copying over a running script risks a
file lock and would otherwise leave the *old* code running until the next
reboot. An upgrade that silently does nothing is worse than one that fails
loudly.

**Refuses `homeassistant.local`.** The installer rejects it and asks for a
numeric IP. mDNS not resolving is the single most common cause of this setup
failing, and it fails in a way that looks like a credentials problem.

**Password is not echoed, and the file is locked down.** Entered as a
SecureString, and `farm-bridge.env` gets an ACL limiting it to Administrators
and SYSTEM.

**Connectivity is tested before declaring success.** A TCP check against the
farm and the broker. A warning here is far cheaper than an operator believing
the install worked and finding out during an incident.

**Runs as SYSTEM at startup**, so it does not need anyone logged in, with
automatic restart on failure and no execution time limit.

**The uninstaller keeps your data by default.** It removes the startup task and
stops the process, but leaves `C:\Farm` alone unless you type `DELETE` at a
second prompt. Silently destroying a working MQTT config is a bad trade for the
two seconds it saves.

## Maintenance

`$PythonFallbackUrl` in `Install-FarmBridge.ps1` is pinned to a specific Python
release, used only when winget is unavailable. Bump it occasionally. winget is
tried first precisely because it does not go stale.

## What the installer does NOT do

It sets up the **bridge**. The Home Assistant side — the alert script, the 20
automations, the dashboards — is still manual, because it is pasted into the HA
UI rather than installed on this PC. The installer ends by pointing at the
README's "New install" section, step 3 onward.

Step 3 is the notification test. It is the step that, when skipped, let ten
dead automations go unnoticed indefinitely on the original farm.
