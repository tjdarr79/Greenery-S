[README.md](https://github.com/user-attachments/files/31240127/README.md)
# Greenery S → Home Assistant MQTT Bridge

Reads live sensor data from a Freight Farms Greenery S container farm's local
Farmhand web app (no cloud dependency) and republishes it to MQTT with Home
Assistant auto-discovery, so sensors appear automatically as a device in HA.

Built after Freight Farms' Chapter 7 bankruptcy (April 2025) and Growcer's
acquisition of Farmhand support — this removes dependency on Farmhand's cloud
dashboard and any paid support tier for basic monitoring.

## Confirmed device map

Reverse-engineered by cross-referencing the SSE payload against the Farmhand
dashboard's live displayed values. Do not change without re-verifying.

| Device ID | Type | Role | Fields |
|---|---|---|---|
| `94E68607D218` | dosing | **Cultivation** | pH, EC, water temp |
| `244CAB0FD624` | dosing | **Nursery** | pH, EC, water temp |
| `94E68607D090` | environmental | Farm climate | CO2, RH, air temp |
| `8C4B14715024` | input | Depth/pressure | analog_1 = Cultivation depth (raw, uncalibrated offset), analog_2 = Nursery depth, analog_3 = left send pressure, analog_4 = right send pressure |
| `244CAB0FC00C` | output | 32-channel relay | **Not yet mapped to function** — not included in this bridge |
| `E831CDC7E680` | input | Idle/unused | All zero, not included |

**Known caveat:** Cultivation depth sensor (`analog_1`) needs recalibration —
raw value does not match Farmhand's displayed % until fixed. Published as
"raw" for this reason. Both tank depth readings running above 100% during
development was traced to continuous gutter return + HVAC condensate return
feeding the tanks — this is normal for this system, not a fault, and is
separate from the calibration offset issue.

## Architecture

```
Farmhand local web app (192.168.200.200:3001/farm-data, SSE)
        ↓
farm_bridge.py (Python, aiohttp + paho-mqtt)
        ↓
MQTT broker (Mosquitto, on Home Assistant instance)
        ↓
Home Assistant (auto-discovered as "Greenery S Farm" device, 12 entities)
```

The SSE stream aborts ~60 seconds server-side (confirmed via DevTools). The
bridge treats this as expected behavior and reconnects automatically — this
is not an error condition.

## Requirements

- Python 3.8+
- Network access to both `192.168.200.200:3001` (Farmhand local app) and your
  MQTT broker (typically the same subnet as Home Assistant)
- An MQTT broker with a valid login — see Setup Gotchas below, this tripped
  up initial setup

## Install

```bash
pip install -r requirements.txt --break-system-packages   # Linux/Mac
pip install -r requirements.txt                            # Windows
```

Copy `farm-bridge.env.example` to `farm-bridge.env` and fill in real MQTT
credentials. **Never commit `farm-bridge.env`** — it's gitignored for this
reason.

## Running

**Manual test first, always** — confirm it works before deploying unattended:

```bash
# Linux/Mac
export FARM_MQTT_USER=your_username
export FARM_MQTT_PASS=your_password
python3 farm_bridge.py

# Windows CMD
set FARM_MQTT_USER=your_username
set FARM_MQTT_PASS=your_password
python farm_bridge.py
```

Look for:
```
MQTT connected: Connected successfully
Published discovery config for Cultivation pH
... (12 total)
```

Then check Home Assistant: **Settings → Devices & Services → MQTT
integration → Devices → "Greenery S Farm"**. Confirm all 12 entities show
live numeric values, not "unknown."

## Deploying unattended

### Linux (systemd)

```bash
sudo cp farm_bridge.py farm-bridge.env /opt/farm-bridge/
sudo cp farm-bridge.service /etc/systemd/system/
sudo systemctl enable --now farm-bridge
```

### Windows (Task Scheduler)

Task Scheduler does not inherit interactive-session environment variables —
set them machine-wide first, from an elevated (Administrator) CMD window:

```cmd
setx FARM_MQTT_USER "your_username" /M
setx FARM_MQTT_PASS "your_password" /M
```

Close and reopen CMD (setx doesn't apply to the current session), then find
your Python interpreter's full path:

```cmd
where python
```

Create the task using the **full absolute path** to python.exe — a bare
`python` command fails under the SYSTEM account with "cannot find the file
specified," since SYSTEM doesn't have your user PATH:

```cmd
schtasks /create /tn "FarmBridge" /tr "\"C:\path\to\python.exe\" \"C:\path\to\farm_bridge.py\"" /sc onstart /ru SYSTEM /rl highest
```

If SYSTEM still can't run it (common with per-user Python installs under
`AppData\Local`), drop `/ru SYSTEM` and let it default to your own account —
Task Scheduler will prompt for your Windows password to store for unattended
runs.

Test immediately without rebooting:

```cmd
schtasks /run /tn "FarmBridge"
```

**Validate with a real cold reboot**, not just a manual task run — confirm
HA entities repopulate with no terminal window open and nobody logged in.
That's the actual failure mode this deployment exists to survive.

## Setup gotchas encountered (keep this section — saves the next debug pass)

- **MQTT auth failures (rc=5, "not authorized"):** the broker login is NOT
  automatically your Home Assistant frontend username/password unless
  "Login with Home Assistant" is explicitly enabled in the Mosquitto add-on
  config. Get the actual valid login from the Mosquitto broker add-on's
  Configuration tab (`logins:` section), don't assume.
- **MQTT integration missing "Devices" tab:** if HA shows no MQTT integration
  card at all under Devices & Services, the broker (Mosquitto) may be
  running but never linked to HA's MQTT *integration*. These are separate —
  broker is the server, integration is HA's client. Add via **+ Add
  Integration → MQTT**.
- **`homeassistant.local` doesn't belong in broker config** — that's an
  mDNS hostname that may not resolve reliably from a script running
  unattended/headless. Use the actual static IP instead.
- **`pip install --break-system-packages`** is Linux/Debian-specific (PEP
  668). Omit entirely on Windows — it will error as an unrecognized flag.
- **`pip`/`python` not recognized in CMD:** try the `py` launcher (`py -m
  pip install ...`, `py farm_bridge.py`) — often correctly registered even
  when `pip`/`python` aren't on PATH.

## Not yet built

- Phone alerts / HA automations on top of these sensor entities
- Output relay function mapping (32 channels, `244CAB0FC00C`)
- Write/control from HA (dosing, lighting, climate) — deliberately deferred;
  manual control mode disables Farmhand's automatic safety shutoffs (e.g.
  trough overflow protection), so any HA-side control automation needs to
  replicate those interlocks before going live
