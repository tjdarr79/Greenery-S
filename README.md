# Greenery S → Home Assistant MQTT Bridge

> **License & Warranty:** Licensed under the [MIT License](LICENSE) — free
> to use, modify, and redistribute, provided as-is with **no warranty of
> any kind, express or implied**. Use at your own risk. This code
> interacts with live agricultural equipment (dosing pumps, climate
> control, water systems) — a bug or misconfiguration could affect a real
> crop. Test thoroughly in your own environment before relying on it, and
> review each file yourself before running it against production
> hardware. The author(s) are not liable for crop loss, equipment damage,
> or any other direct or indirect damages arising from its use.
>
> **Tested on:** Home Assistant Green (HA OS), Windows 11 (bridge script
> host), Android (Companion App + Tailscale). Other platforms and OS
> versions may work but have not been verified — if you get this running
> on iOS, HA OS alternatives, Linux, or macOS, contributions/notes on
> what changed are welcome.

Reads live sensor data from a Freight Farms Greenery S container farm's local
Farmhand web app (no cloud dependency) and republishes it to MQTT with Home
Assistant auto-discovery, so sensors appear automatically as a device in HA.

Built after Freight Farms' Chapter 7 bankruptcy (April 2025) and Growcer's
acquisition of Farmhand support — this removes dependency on Farmhand's cloud
dashboard and any paid support tier for basic monitoring.

## New install — do these in order

Order matters. Each step verifies before the next depends on it.

### 1. Prerequisites

- Python 3.8+ on a machine that stays on and can reach the farm network
- Home Assistant with the **Mosquitto broker** add-on installed and running
- `pip install -r requirements.txt`

### 2. Configure and start the bridge

Copy `farm-bridge.env.example` to `farm-bridge.env`, fill in the MQTT host and
credentials, then run `farm_bridge.py`. See **Install** and **Running** below,
and **Deploying unattended** for systemd / Task Scheduler.

**Verify before continuing:** Settings → Devices & Services → **MQTT** →
**Greenery S Farm**. You should see ~48 entities. Note that the device lives
*inside* the MQTT card — it is not listed at the top level of Devices &
Services, which is easy to miss.

Nothing below works until entities are appearing.

### 3. Install the alert script — and test it

Settings → Automations & Scenes → **Scripts** → + Add Script → ⋮ →
**Edit in YAML** → paste `farm-alerts-script.yaml` → Save.

Change the notify target inside it to your own phone. Find yours at
Settings → Tools → Actions, or render
`{{ states.notify | map(attribute='entity_id') | list }}` in
Settings → Tools → Template.

**Then run it: Scripts → Farm Alerts → ⋮ → Run.** Type any title and message.

If the phone does not buzz, stop here and fix it. Do not install the
automations — they will all fail the same way. This single test is the step
that, when skipped, let ten dead automations go unnoticed indefinitely. See
**Notification routing** for the full explanation.

### 4. Install the automations

Thirteen files in `automations/`, pasted one at a time — HA's editor rejects a
multi-automation list. None of them contain a phone name; that lives only in
the script. See **Installing the automations**.

Start with `01-bridge-offline.yaml`, then verify it with ⋮ → **Run actions**.
If that reaches your phone, the wiring is proven for all thirteen.

Hold back `13-equipment-not-responding.yaml` until the `Relay State Mismatch`
sensor has been quiet for a few days — see the hypothesis note under
**Output board mapping**.

### 5. Add the dashboards

`farm-dashboard.yaml` (desktop) and `farm-dashboard-mobile.yaml` (phone).

### 6. Optional — Task Mode control

`dashboard-controls.yaml` adds Enter/Exit Task Mode buttons with confirmation
guards. Read **Task Mode control** first: the endpoint is undocumented, and the
card must carry `confirmation:` or a pocket-tap will stop the farm.

---

## What is in this repo

| File | Purpose |
|---|---|
| `farm_bridge.py` | The bridge. Sensors, 32-channel relay mapping, and Task Mode control all included — **no patch steps** |
| `farm-bridge.env.example` | Copy to `farm-bridge.env` and fill in |
| `farm-bridge.service` | systemd unit (Linux) |
| `farm-alerts-script.yaml` | The notification hub. **The only file containing a phone name** |
| `automations/01`–`13` | Alert automations, pasted into HA one at a time |
| `farm-dashboard.yaml` | Desktop dashboard |
| `farm-dashboard-mobile.yaml` | Phone dashboard |
| `dashboard-controls.yaml` | Task Mode control card |
| `tools/dump-relay.py` | Diagnostic — inspect the raw output board |
| `tools/discover-farmhand-api.py` | Diagnostic — re-find the control endpoint after a farmhand update |
| `WINDOWS-INSTALL.md` | Windows-specific setup |

Everything under `tools/` is optional and read-only. The bridge does not use
them.

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

## Home Assistant navigation (2026.2.0 and later)

**Developer Tools was removed from the sidebar in HA 2026.2.0.** It now
lives under **Settings → Tools** — scroll or swipe down the Settings list
to find it. Older guides (including earlier revisions of this README) that
say "sidebar → Developer Tools" or "enable Advanced Mode to reveal
Developer Tools" are out of date and will send you looking for a menu
entry that no longer exists.

Direct URLs, which are faster than navigating:

| Tab | URL | Used for |
|---|---|---|
| YAML | `/config/tools/yaml` | Validate config, reload, restart |
| Actions | `/config/tools/action` | Fire a service call by hand — notify tests |
| States | `/config/tools/state` | Inspect/override entity state |
| Template | `/config/tools/template` | Render Jinja before pasting into an automation |

Fastest route of all: press **`c`** anywhere in the UI to open the command
palette and jump straight to a tab.

**Finding the farm's entities:** they live under
**Settings → Devices & Services → MQTT → Greenery S Farm**. The device belongs
to the MQTT integration, so it is *inside* the MQTT card — not listed at the top
level of Devices & Services. Easy to miss when you are looking for it by name.

## Alert automations

Thirteen phone-notification automations live in `automations/`, one file each,
covering the thresholds actually needed for daily operation. They notify
via Home Assistant's Companion App, not email/SMS.

**All of them route through `notify.farm_alerts`, a notify group defined in
`notify-group.yaml`. That group must exist before any automation is
deployed — see "Notification routing" below.**

| File | Fires when | Window | Why |
|---|---|---|---|
| `01-bridge-offline.yaml` | Bridge disconnects | 5 min | If this fires, every other alert below is unreliable until it clears |
| `02-ph-out-of-range.yaml` | pH <5.4 or >6.4, either zone | 5 min | Working target for lettuce/brassicas is 5.5–6.2. Above ~6.5 iron and manganese availability collapses |
| `03-ec-critical.yaml` | EC >2000, either zone | 5 min | Algae/debris on the sensor or a clog. ~1.3× the 1200–1600 target band |
| `04-co2-below-minimum.yaml` | CO2 <500 ppm | 5 min | Tank-empty signal. 5 min gives ~55 min of lead time before starvation risk, based on observed ~1hr decline from 500→300 ppm |
| `05-air-temp-high.yaml` | Air temp ≥80°F | **1 min** | Deliberately short — 80°F means HVAC has already lost the fight and lights need to come off; this is an emergency alert, not a routine one |
| `06-humidity-high.yaml` | Humidity ≥80% | 5 min | Routine watch alert |
| `07-water-temp-high.yaml` | Water temp >72°F (warn) / >75°F (critical) | 30 min / 10 min | Target band 65–68°F. See the measurement caveat below |
| `08-ec-low.yaml` | EC <900, either zone | 15 min | A dead or clogged dosing pump lets EC drift *down* and starves the crop — the failure a high-only alarm never catches |
| `09-co2-high.yaml` | CO2 >2500 ppm | **10 min** | Stuck injection solenoid. Long window deliberately filters tank-swap transients — see below |
| `10-tank-depth-low.yaml` | Nursery depth <20% | 10 min | Running a pump dry destroys it |
| `11-recirc-pump-stopped.yaml` | Either recirc pump off **while in auto** | 5 min | Dosing stops AND the hydro sensors go stale — pH/EC keep reporting plausible numbers for standing water. Gated on Task Mode so cleanouts stay silent |
| `12-task-mode-left-on.yaml` | Task Mode active | 6 hours | Recipe automation suspended. The alert this README had queued and could not build until the relay board was mapped |
| `13-equipment-not-responding.yaml` | Relay state ≠ shadow | 15 min | **Validate before enabling** — see the hypothesis note above |

### Water temp measurement caveat (important)

`07-water-temp-high.yaml` thresholds are set against a **delivery-point**
reading, not bulk tank temperature.

The cultivation dosing sensors (`94E68607D218`) are physically located at
the nursery table, roughly 30 ft downstream of the cultivation tank, fed by
exposed pipe running adjacent to the LED walls. Water picks up heat in
transit. What the sensor reports is the temperature of water *arriving at
the nursery table*, which is what the plants and the dosing system see, but
it is higher than the tank itself by an unmeasured delta.

The nursery loop is separately chiller-controlled at 69°F, so a nursery
trigger on this automation indicates **chiller failure**, not gradual drift.

Until an independent in-tank probe is installed, do not read this sensor as
tank temperature in any diagnosis.

### Why there IS now a high-CO2 alert

Earlier revisions of this README excluded one on the grounds that manual
tank swaps cause expected harmless spikes, and alerting on those trains you
to ignore the notification. **That reasoning is correct and is preserved
here** — it is handled by discriminating on *duration* rather than by
omitting the alert entirely.

A swap spike is transient. A stuck solenoid climbs and stays. The 10-minute
sustained window filters the former while still catching the latter with
wide margin below the 5000 ppm OSHA PEL. In a sealed 40 ft container this is
the only failure mode that endangers a person rather than a crop, which is
why it warrants an alert at all.

Optional hard suppression: create an `input_boolean` named
`farm_maintenance_mode` and flip it on during tank swaps. The automation's
condition is written to pass when that helper does not exist, so it works
as-is without creating one.

**Queued for later, not yet built:** a lower-urgency alert at 78°F (giving
earlier warning before the 80°F emergency point), and a "Task Mode active
>6 hours" alert. Cultivation tank depth is also not yet alarmed —
`analog_1` is uncalibrated (see the note in `farm_bridge.py`), so any
threshold against it would be arbitrary. The trigger is committed
commented-out in `10-tank-depth-low.yaml`, ready once that sensor is
recalibrated.

## Output board mapping (32-channel relay)

Device `244CAB0FC00C` is the farm's actuator layer. `farm_bridge.py` publishes
all 32 channels as **binary sensors, read-only**, plus three derived signals.

**Nothing writes to this board.** These relays are driven by farmhand's recipe
engine. Writing to them makes Home Assistant and the Hub disagree about reality,
and the engine reverts on its next cycle. Any auxiliary control belongs on
separate relay hardware.

### Why this matters

Before this, the farm's *state* was measured (pH, EC, temp, CO2, depth) but its
equipment *behavior* was not. That gap hid a specific failure: a recirc pump
stopping does not just stop a pump — it leaves the hydro sensors reading
standing water, so pH and EC keep reporting plausible numbers that no longer
describe the tank. Every downstream alarm quietly becomes unreliable while
looking fine.

### Channel map

From Freight Farms' *How to Read the Greenery S Electrical Schematic* (Mar 2025).
The CB column is why it is worth having: a tripped breaker maps straight to the
entities that went dark.

| Ch | Equipment | CB | Ch | Equipment | CB |
|---|---|---|---|---|---|
| 1 | Cultivation Recirc Pump | CB5 | 17 | Nursery LED Top Red | CB4 |
| 2 | Left Send Pump | CB5 | 18 | Nursery LED Top Blue | CB4 |
| 3 | Right Send Pump | CB5 | 19 | Nursery LED Bottom Red | CB4 |
| 4 | Cultivation Autofill | CB5 | 20 | Nursery LED Bottom Blue | CB4 |
| 5 | Nursery Autofill | CB5 | 21 | Nursery Work LEDs | CB4 |
| 6 | Nursery Top Trough Pump | CB4 | 22 | Cultivation Work LEDs | CB5 |
| 7 | Nursery Bottom Trough Pump | CB4 | 23 | **Spare — chiller + extra fans** | CB2 |
| 8 | **Nursery Recirc + Chiller Pump** | CB4 | 24 | *unmapped* | — |
| 9 | CO2 Regulator | CB4 | 25–27 | Cultivation LED Left Red | CB6/7 |
| 10 | Duct Fans (L+R) | CB5 | 28 | Cultivation LED Left Blue | CB8 |
| 11 | Overhead Fan | CB4 | 29–31 | Cultivation LED Right Red | CB9/10 |
| 12 | Exhaust Fan | CB4 | 32 | Cultivation LED Right Blue | CB8 |
| 13 | HVAC Blower | CB11 | 16 | *unmapped* | — |
| 14 | HVAC Cooling | CB11 | | | |
| 15 | HVAC Heater | CB11 | | | |

### Two site-specific wiring notes

**Channel 8 also carries the chiller pump.** It shares power with the nursery
recirc pump so Task Mode drops both together during a tank cleanout — this
prevents running the chiller pump against a drained tank. Deliberate. The entity
name says so; don't "tidy" it.

**Channel 23 is held in manual permanently.** It carries the chiller unit and
extra fans. Because it is never `auto`, it is listed in `TASK_MODE_EXCLUDE` in
`farm_bridge.py` — without that, the Task Mode sensor reads true forever and
means nothing.

There is deliberately **no** "chiller running dry" sensor. The chiller is
switched off at the unit before the nursery valve is opened, and that switch is
invisible here — ch23 stays energised regardless. Such a sensor would fire
through every cleanout and train you to ignore it. The meaningful alarm is
`11-recirc-pump-stopped`, gated on Task Mode being off.

### Three derived signals

| Entity | Meaning |
|---|---|
| **Task Mode Active** | Any channel off `auto`, excluding ch23. Recipe control is suspended. |
| **Output Board Connected** | The board's own link state. |
| **Relay State Mismatch** | `state` ≠ `shadow`. **Hypothesis** — `shadow` appears to hold commanded state. Validate over a few days before trusting. `output_24` is absent from `shadow` and excluded. |

### Reading the board yourself

`dump-relay.py` (stdlib only) prints one SSE frame's worth of channel states.
Useful for confirming a mapping or working out what the unmapped channels do.

## Task Mode control (one-tap, human-decided)

Two buttons and a status sensor, published by the bridge over MQTT discovery.
No `configuration.yaml`, no HA restart.

| Entity | Sends |
|---|---|
| `button.enter_task_mode` | `{"command": "enter_mode", "mode": "task_mode"}` |
| `button.exit_task_mode` | `{"command": "exit_mode"}` |
| `sensor.farm_control_status` | Result of the last command |

Endpoint: `POST http://192.168.200.200:3001/farm-control` — captured from the
farmhand local UI with browser DevTools. Overridable via `farm-bridge.env`.

### Deliberately a button, not an automation

Home Assistant never puts the farm in Task Mode on its own. A stuck or
miscalibrated sensor cannot halt production unattended — a human sees the
alert, looks, and decides. Automatic triggering can come later, once the
sensors have earned trust and we have watched how farmhand reacts to an
external mode change.

### It verifies itself

The button does not fire and hope. After sending it waits, then reads the
output board's own `mode` field — the same ground truth behind the
`Task Mode Active` sensor — and reports `CONFIRMED`, or
`NOT CONFIRMED - board still reports auto`.

This closed loop only exists because the output board was mapped first. Without
it there would be no way to know whether a command actually took.

### Put a confirmation on the dashboard card

```yaml
type: button
entity: button.enter_task_mode
confirmation:
  text: Put the farm in TASK MODE? Lights off, recipe suspended.
```

Without this, a pocket-tap on a phone stops the farm.

### Test it deliberately, before you need it

The next tank cleanout puts you in Task Mode anyway. Use the button instead of
the farmhand UI and watch `Farm Control Status` reach `CONFIRMED`. A one-tap
emergency control that has never been fired is a guess, not a control.

### This endpoint is unsupported

`/farm-control` is not a documented Freight Farms API. A farmhand update can
change or remove it. Failures surface in `Farm Control Status` rather than
passing silently, but expect to re-capture the request after farm software
updates. The capture procedure: farmhand local UI → DevTools → Network →
toggle Task Mode → read the request URL, method, and JSON body.

### Emergency path if the bridge is down

The buttons only exist while the bridge is alive — correct, since a dead bridge
means the control could not be trusted. The fallback is the farmhand UI itself,
reachable from a phone over the Tailscale tunnel (see Remote access below).
That path is fully supported and does not depend on any of this.

## Notification routing

### The problem this solves

Every automation used to call `notify.mobile_app_farm_phone` directly.

That service name is **auto-generated** by the Companion App from the
phone's device name at the moment it registers. It is destroyed whenever
the app is reinstalled, the phone is renamed, or the user logs out — Home
Assistant then registers a *new* name, and every automation pointing at
the old one breaks.

That is exactly what happened. The phone re-registered under its raw
Android model number (`mobile_app_sm_s711u`) instead of the friendly name
it had before. All automations went mute — **including
`01-bridge-offline`, the watchdog for the whole pipeline.** Home Assistant
raised a Repair notice for only `05-air-temp-high`, because that was the
one automation that happened to fire. Nothing in this project had ever
verified the notification path was alive, so the failure stayed invisible.

### The fix: one script, one place to change

All ten automations call **`script.farm_alerts`** instead of a phone. That
script is the only thing in the system that knows your phone's name.

```
automations (x10)  ──►  script.farm_alerts  ──►  your phone
                                             └─► HA persistent notification
```

When your phone changes, you edit one line in one script. Nothing else.

The script also writes a **persistent notification** in the Home Assistant
UI on every alert. That runs even when the push fails, so a dead phone
leaves a visible trace instead of silence.

### Installing the alert script (do this FIRST)

No file editing. No restart. No add-ons.

1. **Settings → Automations & Scenes → Scripts** tab
2. **+ Add Script** → three-dot menu (top right) → **Edit in YAML**
3. Delete whatever is in the box, paste the entire contents of
   `farm-alerts-script.yaml`
4. **Save**

### Test it before going any further

Still on the Scripts page: find **Farm Alerts** → three-dot menu → **Run**.

A dialog appears with Title and Message boxes (that is what the `fields:`
block in the script is for). Type anything and run it.

- **Phone buzzes** → the whole chain works. Continue to the automations.
- **Nothing** → stop here. Do not install the automations; they will all
  fail the same way. Fix this first:
  - Settings → Tools → Actions (`/config/tools/action`), type
    `notify.mobile_app` and see what actually autocompletes. If it differs
    from `mobile_app_sm_s711u`, put the real one in the script — it
    appears in **two** places, both marked `# <-- YOUR PHONE`.
  - Settings → Devices & Services → **Mobile App** should list your phone.
    If it lists a stale device too, delete the stale one.

**This test is the single most important step in this document.** It is
the step that was never done, which is why the outage went unnoticed.

### Installing the automations

HA's single-automation YAML editor rejects a multi-automation list (it
throws `Message malformed: extra keys not allowed @ data['0']` if you try)
— each file must be pasted in separately, one at a time:

1. Settings → Automations & Scenes → **+ Add Automation**
2. Skip the visual builder — click the three-dot menu → **Edit in YAML**
3. Paste the full contents of one file (e.g. `01-bridge-offline.yaml`)
4. Save
5. Repeat for the remaining twelve files

Nothing in these files needs editing for your setup. They contain no phone
name — that lives only in the script.

### When your phone changes (the thing that broke this before)

1. Settings → Tools → Actions → type `notify.mobile_app` → note the new name
2. Settings → Automations & Scenes → Scripts → **Farm Alerts** → Edit in YAML
3. Replace the name in both `# <-- YOUR PHONE` lines
4. Save, then Run the script to confirm the phone buzzes

Four steps, one file. Previously this meant editing ten automations, and
forgetting one meant a silent gap.

### Adding a second phone

One notification path is a single point of failure for a container holding
perishable inventory. To notify two devices, add a second action beside
each existing one in the script:

```yaml
      - action: notify.mobile_app_sm_s711u
        continue_on_error: true
        data:
          title: "{{ title }}"
          message: "{{ message }}"
      - action: notify.mobile_app_second_phone
        continue_on_error: true
        data:
          title: "{{ title }}"
          message: "{{ message }}"
```

`continue_on_error: true` on each means one dead phone cannot stop the
other from being notified.

### Alternative: a notify group in configuration.yaml

If you prefer YAML config over a UI script, the same decoupling can be
done with a notify group:

```yaml
notify:
  - platform: group
    name: farm_alerts
    services:
      - action: mobile_app_sm_s711u
```

Then change `script.farm_alerts` to `notify.farm_alerts` in the
automations. This requires editing `/config/configuration.yaml` (needs the
File Editor, Studio Code Server, or Samba add-on on HA OS) and a **full
restart** — a config-defined notify group is not hot-reloadable. It also
does not give you the persistent-notification fallback unless you add that
step back into every automation.

The script route is the documented default because it needs none of that.

### Testing the automations themselves

Once the script is proven, two more things can be broken independently:

**Does the automation's action block reach the phone?** Settings →
Automations & Scenes → select the automation → ⋮ → **Run actions**. This
fires the actions immediately, ignoring the trigger. Note that
`trigger.to_state` templates render empty on a manual run — that is
expected, not a fault.

**Does the trigger actually fire?** `numeric_state` triggers only fire on a
*crossing*. If a sensor is already past the threshold when you save the
automation, it will NOT fire retroactively just because the condition is
already true. To test for real, temporarily set the threshold just past
the sensor's *current* reading, save, wait out the `for:` window, confirm
the phone buzzes, then set it back and save again.

Example: if air temp is reading 71°F and you want to test
`05-air-temp-high.yaml`, temporarily change `above: 80` to `above: 71.5`
— not `above: 60`, which is already true and won't trigger anything.

If a test produces no notification and you're unsure whether the trigger or
the delivery is at fault, check Settings → Automations → open the
automation → **Traces**.


## Remote access (off local network)

Local access via the Companion App works automatically once you're on the
same wifi as Home Assistant. To check the farm from anywhere else (home,
cell data, traveling), use **Tailscale** — a free outbound-only VPN that
avoids opening any inbound port on your router. Port forwarding was
deliberately ruled out for this project due to the security exposure of
opening a firewall port directly to the internet.

### Setup

1. **Install the Tailscale add-on on Home Assistant** (HA Green/HA OS):
   Settings → Add-ons → Add-on Store → search "Tailscale" → install the
   official Home Assistant Community Add-ons version → start it → enable
   "Start on boot" and "Watchdog."
2. Open the add-on's **Log** tab, find the login URL it prints on first
   start, open it in a browser, sign in / create a free Tailscale account,
   authorize the device.
3. **Install the Tailscale app** on your phone (App Store / Play Store),
   sign in with the same account.
4. Confirm both HA Green and your phone show as connected devices at
   tailscale.com/admin.
5. Get HA Green's Tailscale IP from that admin console (starts with
   `100.x.x.x`).
6. In the **Home Assistant Companion App** on your phone (this is the
   app's own name — not to be confused with the HA app in general): if
   you can't reach your existing server, it will show an "Unable to
   connect" screen with a **Settings** link → tap it → **Add Server** →
   enter `http://100.x.x.x:8123` (the Tailscale IP from step 5) → log in.

### Gotchas encountered

- **Battery optimization can silently kill the Tailscale app** in the
  background on both Android and iOS. Exclude it from any battery-saver
  restrictions, or the tunnel drops without warning when you're not
  actively looking at the app.
- **No cell signal inside the Greenery S container itself** — the metal
  shipping container blocks cell reception, same as any Faraday-cage-like
  enclosure. If you're testing remote access while physically standing
  inside the farm, it will fail even with everything configured
  correctly — not a bug, just physics. Step outside to test.
- The mobile app is officially called the **Companion App** in Home
  Assistant's own settings/menus — worth knowing when searching HA's docs
  or forums for help, since "the app" and "Companion App" are used
  interchangeably but only the latter shows up in menu labels.



- 78°F early-warning temp alert, and Task Mode >6hr alert (queued, see
  Alert Automations section above)
- Output relay function mapping (32 channels, `244CAB0FC00C`)
- Write/control from HA (dosing, lighting, climate) — deliberately deferred;
  manual control mode disables Farmhand's automatic safety shutoffs (e.g.
  trough overflow protection), so any HA-side control automation needs to
  replicate those interlocks before going live
- Mobile-optimized dashboard layout — current layout targets desktop width
