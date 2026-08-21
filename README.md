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

## Alert automations

Six phone-notification automations live in `automations/`, one file each,
covering the thresholds actually needed for daily operation. They notify
via Home Assistant's Companion App, not email/SMS.

| File | Fires when | Window | Why |
|---|---|---|---|
| `01-bridge-offline.yaml` | Bridge disconnects | 5 min | If this fires, every other alert below is unreliable until it clears |
| `02-ph-out-of-range.yaml` | pH <5.0 or >7.75, either zone | 5 min | 7.75 ceiling accounts for normal post-fill readings (~7.5) that would false-trigger at a stricter 7.0 |
| `03-ec-critical.yaml` | EC >3000, either zone | 5 min | Documented signal for algae/debris on the sensor or a clog |
| `04-co2-below-minimum.yaml` | CO2 <500 ppm | 5 min | Tank-empty signal. 5 min gives ~55 min of lead time before starvation risk, based on observed ~1hr decline from 500→300 ppm |
| `05-air-temp-high.yaml` | Air temp ≥80°F | **1 min** | Deliberately short — 80°F means HVAC has already lost the fight and lights need to come off; this is an emergency alert, not a routine one |
| `06-humidity-high.yaml` | Humidity ≥80% | 5 min | Routine watch alert |

**Not included, by design:** no alert on CO2 spiking *high*. Manual tank
swaps cause expected, harmless spikes — alerting on those would just
train you to ignore the notification. Only the low-CO2/empty-tank
condition matters operationally.

**Queued for later, not yet built:** a lower-urgency alert at 78°F (giving
earlier warning before the 80°F emergency point), and a "Task Mode active
>6 hours" alert.

### Installing the automations

HA's single-automation YAML editor rejects a multi-automation list (it
throws `Message malformed: extra keys not allowed @ data['0']` if you try)
— each file must be pasted in separately, one at a time:

1. Settings → Automations & Scenes → **+ Add Automation**
2. Skip the visual builder — click the three-dot menu → **Edit in YAML**
3. Paste the full contents of one file (e.g. `01-bridge-offline.yaml`)
4. Save
5. Repeat for the remaining five files

**Before installing any of them:** replace `notify.mobile_app_farm_phone`
in every file with your own phone's actual notify service name — this is
specific to how your device registered in the Companion App, not a fixed
value. Find yours: enable Advanced Mode (click your profile icon,
bottom-left → scroll down → toggle **Advanced Mode** on) to reveal
Developer Tools, then **Developer Tools → Actions**, search "notify" —
your device's service name appears in the results.

### Testing before you trust them

Two separate things can be broken independently — test them in this
order so you know which one you're actually debugging:

**1. Confirm notification delivery works at all**, independent of any
automation logic:
- Developer Tools → Actions → search "notify" → select your device's
  service
- In the data field, enter:
  ```yaml
  title: Test Alert
  message: This is a manual test
  ```
- Click **Perform Action**. Phone should buzz within seconds.

If this fails, the automations aren't the problem — the notify service
name is wrong, or the Companion App isn't fully registered
(Settings → Devices & Services → Mobile App should list your phone).

**2. Confirm each trigger actually fires**, once delivery is proven:
`numeric_state` triggers only fire on a *crossing* — if a sensor is
already above/below the threshold when you save the automation, it will
NOT fire retroactively just because the condition is already true. To
test for real, temporarily set the threshold to a value just past the
sensor's *current* reading, save, wait out the `for:` window, confirm
the phone buzzes, then set the threshold back to its real value and save
again.

Example: if cultivation is reading 71°F and you want to test
`05-air-temp-high.yaml`, temporarily change `above: 80` to `above: 71.5`
— not `above: 60`, which is already true and won't trigger anything.

Check Settings → Automations → open the automation → **Traces** tab to
confirm whether it fired at all, if a test doesn't produce a
notification and you're unsure whether the trigger or the delivery is
at fault.


## Not yet built

- 78°F early-warning temp alert, and Task Mode >6hr alert (queued, see
  Alert Automations section above)
- Output relay function mapping (32 channels, `244CAB0FC00C`)
- Write/control from HA (dosing, lighting, climate) — deliberately deferred;
  manual control mode disables Farmhand's automatic safety shutoffs (e.g.
  trough overflow protection), so any HA-side control automation needs to
  replicate those interlocks before going live
- Remote access without paid hosting (port forwarding + Dynamic DNS,
  ideally behind a reverse proxy with HTTPS rather than raw HTTP exposed
  directly) — local network access via the Companion App is confirmed
  working; off-network access is not yet configured
- Mobile-optimized dashboard layout — current layout targets desktop width
