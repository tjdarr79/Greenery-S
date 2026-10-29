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

### Before anything: which repository URL

| This install is… | Add this repository | Gets |
|---|---|---|
| **A customer farm** | `https://github.com/tjdarr79/Greenery-S#stable` | Releases only — code already proven on 3 Corners Farm |
| **3 Corners Farm** (the dev farm) | `https://github.com/tjdarr79/Greenery-S` | Every change on `main`, first |

Customer farms: [add the stable repository with one click](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftjdarr79%2FGreenery-S%23stable) — or paste the
URL by hand, `#stable` included. Without `#stable` the farm tracks `main` and is
offered every development change.

Get it right at the first install. Supervisor refuses to remove a repository
while an app from it is installed, so moving a farm between channels later
means uninstalling and reinstalling the app. How branch selection was confirmed
is under **Release channel** in `greenery-bridge/DOCS.md`.

**Developing?** Never commit to or work from `stable` — it is what customers
run. Work on `main`; releases follow `CONTRIBUTING.md`.

### 1. Install the bridge — run it on Home Assistant itself

The bridge runs as a Home Assistant **app** on Home Assistant OS — HA Green,
Yellow, a Pi, or an x86 box. No separate computer.

1. **Settings → Apps** (Add-ons in older releases) **→ Store → ⋮ → Repositories**
2. Add the URL from the table above — `#stable` for a customer farm
3. Install **Greenery S Farm Bridge**
4. **Configuration** tab → set **Farm name** (e.g. `Smith Farm`) → **Save**
5. Press **Start**

**Set Farm name before the first start.** It names the device and decides every
entity ID (`Smith Farm` → `sensor.smith_farm_cultivation_ph`). Changing it after
entities exist does not rename their IDs, and bringing the IDs back in line
breaks every automation that uses them — see **Farm name** in
`greenery-bridge/DOCS.md`. Left at the default `Greenery S Farm`, everything
behaves exactly as it did before the option existed.

If the Mosquitto broker app is installed and running, the bridge takes the
broker address and credentials from Home Assistant automatically — **there is
nothing to type**. No IP, no username, no password.

That one property removes the single most failure-prone step in this whole
setup: the `homeassistant.local` trap and wrong MQTT credentials, which between
them account for most of the "Setup gotchas" below.

You also get config through a UI form, logs in the HA Logs tab, automatic
start with a watchdog, inclusion in HA backups, and an Update button when the
repository moves forward.

See `greenery-bridge/DOCS.md` for options and troubleshooting.

No Home Assistant OS at the site? See **Running the bridge outside Home
Assistant** further down. The old Windows installer is retired to
`legacy/windows-installer/` and is not for new installs.

### 2. Verify the device, then prepare this farm's YAML

**Settings → Devices & Services → MQTT → your farm name.** You should see ~54
entities. The device lives *inside* the MQTT card — it is not listed at the top
level of Devices & Services, which is easy to miss. The app's **Log** tab shows
the entity ID prefix this farm got.

Nothing below works until entities are appearing.

Then, on a computer with a clone of this repository — checked out at the same
branch the farm tracks:

```
python tools/verify-repo.py
```

Read-only. Confirms every file is present, each feature's code actually landed,
all YAML parses, and no stale files survived. If it does not print
`ALL CHECKS PASSED`, fix that before configuring anything.

**Any Farm name other than the default:** render this farm's copies of the
automations and dashboards, and paste *those* in steps 4–6, never the repo
originals:

```
python tools/render-farm-yaml.py "Smith Farm"
```

The originals name `greenery_s_farm_*` entities, which do not exist on a farm
with another name — and an automation whose trigger entity does not exist never
fires and never errors. The tool tells you how to confirm its prefix against
Home Assistant before you paste.

### 3. Install the alert script — and test it

**First, the Farm Name helper** — once per install. Settings → Devices &
Services → **Helpers** → + Create Helper → **Text**, named exactly
`Farm Name` (→ `input_text.farm_name`). Open it and type the farm's name —
**the same name as the app's Farm name option**. Nothing links the two, so a
mismatch means alerts say one farm and the dashboards another. Details in
`watchdog-helpers.yaml`.

Every alert title starts with it: `Smith Farm: Water Temp High`. That is how
someone responsible for several farms knows from the banner alone which one
paged them. No helper, or a blank one, just means no prefix — nothing fails.

Then: Settings → Automations & Scenes → **Scripts** → + Add Script → ⋮ →
**Edit in YAML** → paste `farm-alerts-script.yaml` → Save.

Change the notify target inside it to your own phone. Find yours at
Settings → Tools → Actions, or render
`{{ states.notify | map(attribute='entity_id') | list }}` in
Settings → Tools → Template.

**Then run it: Scripts → Farm Alerts → ⋮ → Run.** Type any title and message.
The phone's banner should read `<Farm Name>: <your title>`.

If the phone does not buzz, stop here and fix it. Do not install the
automations — they will all fail the same way. This single test is the step
that, when skipped, let ten dead automations go unnoticed indefinitely. See
**Notification routing** for the full explanation.

### 4. Install the automations

Twenty-four files in `automations/`, pasted one at a time — HA's editor rejects a
multi-automation list. None of them contain a phone name; that lives only in
the script. See **Installing the automations**.

Start with `01-bridge-offline.yaml`, then verify it with ⋮ → **Run actions**.
If that reaches your phone, the wiring is proven for all of them.

Hold back `13-equipment-not-responding.yaml` until the `Relay State Mismatch`
sensor has been quiet for a few days — see the hypothesis note under
**Output board mapping**.

### 5. Add the dashboards

`farm-dashboard.yaml` (desktop) and `farm-dashboard-mobile.yaml` (phone).
Both carry a **Dosing Tanks** card under Environmental, with the four
**Refilled** buttons — see **Dosing tank levels**.

`dashboard-cameras.yaml` is the **Cameras** card, on a view of its own — see
**Cameras (latest photo only)**.

### 6. Optional — Task Mode control

`dashboard-controls.yaml` adds a **Cool-Down** card at the top, Enter/Exit
Task Mode buttons with confirmation guards, a separate **Pumps (Task Mode
only)** card, a **24-Hour Settings** card, and a **Cameras** button that
opens the Cameras view. Read **Task Mode control**,
**Pump switches (Task Mode only)**, **Cool-Down (phone only)** and **24-hour
settings switches** first: the commands are undocumented, and every card must
carry `confirmation:` or a pocket-tap will stop the farm.

---

## What is in this repo

| File | Purpose |
|---|---|
| `farm_bridge.py` | The bridge. Sensors, 32-channel relay mapping, Task Mode control, Task-Mode-only pump switches, Cool-Down, 24-hour settings switches, the farmhand version, the dosing tank levels and the latest camera photos all included — **no patch steps** |
| `farm-bridge.env.example` | Copy to `farm-bridge.env` and fill in |
| `farm-bridge.service` | systemd unit (Linux) |
| `farm-alerts-script.yaml` | The notification hub. **The only file containing a phone name** |
| `automations/01`–`24` | Alert automations, pasted into HA one at a time |
| `watchdog-helpers.yaml` | Helper setup: Farm Name (alert titles), plus helpers + Ping for the CloudGate watchdog |
| `farm-dashboard.yaml` | Desktop dashboard, with the Dosing Tanks card under Environmental |
| `farm-dashboard-mobile.yaml` | Phone dashboard, the same cards stacked |
| `dashboard-controls.yaml` | Control card: Cool-Down, Task Mode, the Task-Mode-only pump switches, 24-hour settings, a button to the Cameras view |
| `dashboard-cameras.yaml` | Cameras card: the latest photo from each of the six cameras, for its own view |
| `tools/dump-relay.py` | Diagnostic — inspect the raw output board |
| `tools/discover-farmhand-api.py` | Diagnostic — re-find the control endpoint after a farmhand update |
| `tools/verify-repo.py` | **Run after any merge** — confirms the clone is complete and correct |
| `tools/render-farm-yaml.py` | Writes a farm's own copy of every automation and dashboard, for any farm name other than the default |
| `tools/farmhand/` | Run-by-hand farmhand scripts: read-only dumps of settings and camera photos, a read-only dosing tank level check, an HVAC logger, and the 24-hour switch for when HA is down — see its README |
| `tests/` | `pytest -q tests` — the dosing tank logic, and the camera code against real farmhand captures in `tests/fixtures/farmhand/`. Without pytest, the dosing tests alone: `python -m unittest discover -s tests -p "test_dosing*.py" -q` |
| `greenery-bridge/` | **The Home Assistant app** — how the bridge is installed |
| `repository.yaml` | Makes this repo installable as an HA add-on repository |
| `CONTRIBUTING.md` | Branches and releases: `main` for development, `stable` for customer farms |
| `legacy/windows-installer/` | **Retired** Windows installer and manual Windows guide. Reference and emergency fallback only |

Everything under `tools/` is optional, and read-only except
`tools/farmhand/set_24hr.py`, which writes the 24-hour settings. The bridge
does not use them.

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
| `E831CDC7E680` | input | **Dosing tank levels** | digital_1 = Nutrient A, digital_2 = Nutrient B, digital_3 = pH Down, digital_4 = Boost / pH Up. 0 = ok — see **Dosing tank levels** |

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

## Running the bridge outside Home Assistant

Only for a site where Home Assistant OS cannot host the app — for example HA
Container, or a broker on another machine. Everything in **New install** from
step 2 onward still applies; only step 1 is replaced by what follows. Windows
hosts: the retired installer in `legacy/windows-installer/` still works, but is
unmaintained.

### Requirements

- Python 3.8+
- Network access to both `192.168.200.200:3001` (Farmhand local app) and your
  MQTT broker (typically the same subnet as Home Assistant)
- An MQTT broker with a valid login — see Setup Gotchas below, this tripped
  up initial setup

### Install

```bash
pip install -r requirements.txt --break-system-packages   # Linux/Mac
pip install -r requirements.txt                            # Windows
```

Copy `farm-bridge.env.example` to `farm-bridge.env` and fill in real MQTT
credentials. **Never commit `farm-bridge.env`** — it's gitignored for this
reason.

### Running

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

### Deploying unattended

#### Linux (systemd)

```bash
sudo cp farm_bridge.py farm-bridge.env /opt/farm-bridge/
sudo cp farm-bridge.service /etc/systemd/system/
sudo systemctl enable --now farm-bridge
```

#### Windows (Task Scheduler)

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

Twenty-four phone-notification automations live in `automations/`, one file each,
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
| `14-cloudgate-watchdog.yaml` | Internet down | 10 min | Power-cycles CloudGate — see CloudGate section |
| `15-missed-alerts-replay.yaml` | Internet restored | 30 s | Re-sends an alert that could not be delivered during an outage |
| `16-module-offline-critical.yaml` | Environmental or Output module offline | 5 min | **Crop emergency.** Farm refuses HVAC/LEDs, or no equipment gets power at all |
| `17-module-offline-dosing.yaml` | Either dosing module offline | 5 min | No dosing occurs, and the pH/EC alarms cannot catch it — same module, stale readings |
| `18-air-temp-low.yaml` | Air temp <55 °F | 15 min | Heater failure or tripped CB11. Only the high half existed before |
| `19-hvac-cooling-stuck.yaml` | HVAC cooling continuous | 24 h | Not reaching setpoint — refrigerant, condenser, or a load it cannot match |
| `20-send-pump-pressure.yaml` | Send pump on with low line pressure | 2 min | Clog, air lock, or failed impeller. Crop stops being irrigated |
| `21-cool-down-fault.yaml` | Cool-Down Fault turns on | immediate | **Critical.** A recirc or chiller pump not confirmed running during Cool-Down — the chiller may run with no flow. Task Mode silences 11, so this is the only recirc alarm then |
| `22-farmhand-version-changed.yaml` | farmhand Version changes | immediate | Task Mode, pump switches, Cool-Down and 24-hour switches use unsupported commands — re-test them |
| `23-dosing-tank-alert.yaml` | The bridge sends a dosing tank alert | as sent | Tank low (every 12 h while low), sensor unstable (at most daily), sensor silent 21 days (weekly). Timing is decided in the bridge — see **Dosing tank levels** |
| `24-camera-stale.yaml` | Camera Photos Stale turns on | immediate, at most once per 24 h | A camera's latest photo is over 2.5 photo cycles (7.5 h) old — the camera or farmhand's photo cycle has stopped. Not critical |

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

**Outside Task Mode, nothing writes to this board.** These relays are driven by
farmhand's recipe engine. Writing to them makes Home Assistant and the Hub
disagree about reality, and the engine reverts on its next cycle. Any auxiliary
control belongs on separate relay hardware. The one exception is three pumps,
switchable only while the recipe is suspended in Task Mode, through farmhand's
own manual command — see **Pump switches (Task Mode only)**.

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

## Pump switches (Task Mode only)

Four switches, published by the bridge over MQTT discovery, turn single pumps
on and off — for a tank cleanout, without walking to the farmhand screen.

| Entity | Relay channel | Tested |
|---|---|---|
| `switch.greenery_s_farm_cultivation_recirc_pump_switch` | 1 — Cultivation Recirc Pump | 2026-09-25 |
| `switch.greenery_s_farm_left_send_pump_switch` | 2 — Left Send Pump | 2026-09-25 |
| `switch.greenery_s_farm_right_send_pump_switch` | 3 — Right Send Pump | 2026-09-25 |
| `switch.greenery_s_farm_nursery_recirc_and_chiller_pump_switch` | 8 — Nursery Recirc and Chiller Pump | 2026-10-16 |

Channel 8 also powers the chiller pump: switching it off stops the chiller's
water flow.

Each sends the same request the farmhand UI sends, captured with DevTools and
confirmed on this farm (the pumps physically ran; for ch8 the capture tool
showed `output_8`, and only `output_8`, change on and off):

```
POST http://192.168.200.200:3001/farm-control
{"command":"manual_action","module":"244CAB0FC00C",
 "output":"output_2","type":"set_output","value":"on"}
```

### Only in Task Mode

Outside Task Mode the recipe engine owns the relays, so the switches show
**unavailable**. The bridge publishes `farm/manual_control/available` —
`online` only while `Task Mode Active` is on — and each switch needs both it
and the bridge itself online.

farmhand ends the data stream about once a minute. The switches keep the last
known Task Mode state through that reconnect, and go unavailable only when no
output-board data has arrived for 30 seconds, or when the bridge itself is
offline. A frame that arrives *without* the output board greys them out at
once.

Greyed-out is not the guard. A command that arrives anyway — from an
automation, or published by hand — is checked again at the moment it is
handled, against output-board data no older than 30 seconds. Outside Task Mode
it is refused and nothing is sent:

`Left Send Pump: REFUSED - farm not in Task Mode`

"Task Mode" here means what `Task Mode Active` means: any channel off `auto`,
ch23 aside. Holding some other channel in manual from the farmhand UI opens the
switches too.

### farmhand's reply proves nothing

`/farm-control` answers `Control message received!` to every command,
including read-only ones and ones it ignores. So the bridge does not trust it:
after sending, it watches the relay's own state in the SSE stream (the same
data behind the channel's binary sensor) for up to 10 seconds, counting only
frames that arrive after farmhand took the command. **Farm Control Status**
then reads one of:

| Status | Meaning |
|---|---|
| `Left Send Pump ON: CONFIRMED in 2.1s` | The relay reports the new state |
| `Left Send Pump ON: NOT CONFIRMED - relay still reports OFF` | farmhand took the command; the relay did not move |
| `Left Send Pump ON: NOT CONFIRMED - no update from the output board in 10s` | The data stream went quiet — the result is unknown |
| `Left Send Pump: REFUSED - …` | Not sent at all |

The switch itself is never optimistic: it shows the relay's reported state,
so a command that did not take leaves it where it was.

### What it never does

- **Sends no command at startup.** Restarting the bridge in Task Mode sends
  no control command. The only requests it sends unprompted are three
  read-only queries farmhand's own page sends on every load — `get_farm_info`
  and `get_version` at start and every 5 minutes (see **farmhand reply
  stream**), and `get_last_images_local_only` at start and every 30 minutes
  (see **Cameras**).
- **Never replays a retained command.** The broker re-delivers a retained
  message on every reconnect. The bridge ignores any command that arrives
  retained, logs a warning and clears it from the broker. When publishing a
  test command by hand, leave **Retain** off.
- **Runs one command at a time.** Two presses — pump, Task Mode button, any
  mix — run one after the other, never interleaved. Commands run off the MQTT
  thread, so a 10-second verify does not stall the bridge.
- **Does not time out a pump.** A switch left on runs until someone turns it
  off. In Task Mode farmhand's automatic safety shutoffs (such as trough
  overflow protection) are not running. Stay with the farm while a pump you
  started is on.

### Adding a channel

A channel becomes switchable only after it has been physically tested. In
order:

1. In Task Mode, switch it on and off **from the farmhand UI** and watch the
   equipment respond.
2. Add its number to `MANUAL_CONTROL_CHANNELS` in `farm_bridge.py`, copy the
   file over `greenery-bridge/farm_bridge.py`, bump the version, and add a tile
   to the pumps card with both confirmations (see the card's header).
3. In Task Mode, switch it on **and** off from Home Assistant, watch the
   equipment, and see `CONFIRMED` both ways — before anyone relies on it.

Never a `TASK_MODE_EXCLUDE` channel (ch23): the bridge refuses to start with
one listed, and `verify-repo.py` fails. Taking a channel off the list removes
its switch from Home Assistant at the next bridge start.

### Acceptance test — at a planned cleanout

1. Farm in **auto**: the four switches show unavailable. In **Settings →
   Devices & Services → MQTT → Configure**, publish `ON` to
   `farm/output_2/set` (Retain off): status reads `REFUSED`, the relay does not
   change.
2. Farm in **Task Mode**: each switch ON gives `CONFIRMED` within 10 s and the
   pump physically runs; OFF gives `CONFIRMED` and it stops.
3. Restart the app while in Task Mode: the Log shows no command sent.
4. Leave one pump ON by hand, then exit Task Mode, and record below whether
   farmhand returns it to auto.

**Does Exit Task Mode return a hand-set pump to auto?** For the recirc pumps,
yes: the owner's routine is to enter Task Mode and switch on output_1 and
output_8 by hand, and on exit both keep running — they are back on auto,
which runs them (recorded 2026-10-16; Cool-Down's End relies on it and checks
it every time). Not yet recorded for the send pumps, 2 and 3. If one stays in
manual after an exit, `Task Mode Active` stays on, **Exit Task Mode** reports
`NOT CONFIRMED`, `11-recirc-pump-stopped` stays silenced, and
`12-task-mode-left-on` pages after 6 hours.

## Cool-Down (phone only)

When the farm runs hot in the day: one tap stops the grow lights and keeps
water moving through the chiller. **Start Cool-Down** puts the farm in Task
Mode (cultivation LEDs off, recipe suspended), then switches relays 1 and 8
back on — cultivation recirc, and nursery recirc with the chiller pump.
**End Cool-Down** exits Task Mode and the recipe resumes.

It is **not for cleanouts** — cleanouts are run from farmhand Local and never
use these buttons — and it is **never automatic**: the bridge only starts or
ends a cool-down when a person presses the button, from the phone.

| Entity | |
|---|---|
| `button.greenery_s_farm_start_cool_down` | Start |
| `button.greenery_s_farm_end_cool_down` | End |
| `binary_sensor.greenery_s_farm_cool_down_active` | On while a cool-down is in force |
| `binary_sensor.greenery_s_farm_cool_down_fault` | A pump could not be confirmed running — drives `21-cool-down-fault` |

Every step is confirmed from the output board, as for the pump switches — never
from farmhand's reply. **Farm Control Status** shows the progress and the
result:

| Status | Meaning |
|---|---|
| `Cool-Down: ACTIVE - lights off, recirc and chiller pump running` | Task Mode confirmed, relays 1 and 8 report ON, every cultivation LED relay reports OFF |
| `Cool-Down: FAILED - Task Mode not confirmed` | The board never left auto. Fault on |
| `Cool-Down: FAULT - <pump> not running` | Still not ON after one retry. Fault on; the farm is **left in Task Mode** for you to decide |
| `Cool-Down: FAULT - <pump> stopped` | During a cool-down the pump reported OFF for 60 s straight. Fault on |
| `Cool-Down: ENDED - recipe resumed` | Task Mode exit confirmed, and both pumps report ON within 15 s |
| `Cool-Down: FAULT - <pump> stopped after exit` | Exit confirmed, but the pump did not come back on auto. Fault on |
| `Cool-Down: NOT ENDED - Task Mode exit not confirmed, cool-down still active` | Press End again, or exit in farmhand |
| `Cool-Down: ENDED - farm left Task Mode without End Cool-Down` | Exited some other way — farmhand, or the Exit Task Mode button. Active and Fault go off |
| `Cool-Down: REFUSED - no recent data from the output board` | Nothing sent |

If the farm was already in Task Mode with the cultivation LEDs off, Start
skips the mode change and only switches the pumps. If any cultivation LED relay
still reports ON at the end, the status says so instead of "lights off".

**Restarting the bridge** sends nothing. Cool-Down Active comes back on only if
it was on before the restart **and** the farm is still in Task Mode; otherwise
it goes off. Cool-Down Fault keeps its last value.

The fault alert is **critical**, because in Task Mode the chiller unit (ch23)
stays powered: with ch8 stopped it may be running with no water flow. Task Mode
silences `11-recirc-pump-stopped`, so during a cool-down the watchdog is the
only alarm for a stopped recirc pump. The bridge clears the fault at the start
of each Start and each successful End, so a new fault always pages once.

### Acceptance test — on a normal day, watching the farm

1. **Start Cool-Down** from the phone: the farm enters Task Mode, cultivation
   LEDs go off, relays 1 and 8 confirmed running, status `ACTIVE`.
2. With it active, switch relay 8 off from farmhand Local: within about 60 s
   Fault turns on and a critical alert arrives.
3. **End Cool-Down**: the farm exits Task Mode, relays 1 and 8 still running,
   status `ENDED`, Active off.
4. Restart the bridge during a cool-down: the Log shows no command sent;
   Cool-Down Active still matches the farm.

## 24-hour settings switches

Three switches for farmhand's own 24-hour settings — the same flags the cloud
app toggles, which keep a nursery trough lit, or cultivation watering cycling,
through the recipe's night. **They replace the cloud app's toggle once the
subscription ends.** They are recipe settings for normal operation, so they
work in auto; Task Mode does not gate them.

| Entity | farmhand settings |
|---|---|
| `switch.greenery_s_farm_nursery_top_24_h` | `nursery_top_red_24hr_enable`, `nursery_top_blue_24hr_enable` |
| `switch.greenery_s_farm_nursery_bottom_24_h` | `nursery_bottom_red_24hr_enable`, `nursery_bottom_blue_24hr_enable` |
| `switch.greenery_s_farm_cultivation_watering_24_h` | `cultivation_left_water_24hr_enable`, `cultivation_right_water_24hr_enable` |

A switch sends the command farmhand Local sends when it saves a recipe card — a
**partial** update, only the keys sent change (verified by a before/after dump,
2026-10-15):

```
{"command":"bulk_update_settings",
 "settings":{"nursery_bottom_red_24hr_enable":1,"nursery_bottom_blue_24hr_enable":1}}
```

**Those six keys are the only settings the bridge can ever write.** Every
command passes one check that refuses a `bulk_update_settings` with any other
key, or any value but 0 or 1, and logs it. `verify-repo.py` fails if the list
changes, or if it differs from `tools/farmhand/set_24hr.py`'s.

**The switch shows what the Hub reports, never what was asked.** State comes
from `get_farm_info` — the Hub's live running settings — read every 5 minutes
and after every change. ON means both flags in the group are 1, OFF both 0.
If they differ (red on, blue off) the switch shows OFF and **Farm Control
Status** reads `Nursery Top 24 h: MIXED - red/blue differ, check farmhand`.
On a new install it shows unknown until the first reply. Until the
subscription ends in November the cloud app can change these too; the switch
follows within 5 minutes.

After a change the bridge asks for the settings again for up to 10 seconds:
`Nursery Bottom 24 h ON: CONFIRMED in 1.2s`, or
`NOT CONFIRMED - farm reports nursery_bottom_red_24hr_enable=0, …`. A change is
refused — nothing sent — if the bridge is not connected to farmhand's reply
stream, because it could not be read back.

### Acceptance test

1. The three switches match `py -3 set_24hr.py status` (see
   `tools/farmhand/README.md`).
2. At farm night: **Nursery Bottom 24 h** ON — the bottom nursery LEDs come on,
   `CONFIRMED`; OFF — they go off, `CONFIRMED`. The top trough is unaffected.
3. **Cultivation Watering 24 h** toggles night cycling for both send pumps; the
   farm reports both the left and right flags changed.

## Dosing tank levels

The four 1-gallon dosing tanks each have an optical level sensor that trips
when the tank is almost empty. farmhand shows them on its Environmental card;
the bridge reads the same four inputs from module `E831CDC7E680` and the
dashboards show them under **Environmental**, in farmhand's order:

| Tank (owner's product) | Input | Entities |
|---|---|---|
| Nutrient A (Form) | `digital_1` | `binary_sensor.greenery_s_farm_nutrient_a_level`, `…_nutrient_a_sensor_fault`, `sensor.…_nutrient_a_days_since_refill`, `button.…_nutrient_a_refilled` |
| Nutrient B (Grow) | `digital_2` | `…_nutrient_b_*` |
| pH Down | `digital_3` | `…_ph_down_*` |
| Boost / pH Up (Boost) | `digital_4` | `…_boost_ph_up_*` |

Owner tests on 2026-10-29 (`tools/farmhand/dosing_levels.py`) set the design:

| Finding | Design |
|---|---|
| 0 = ok; low assumed 1, not yet seen | `dosing_level_low_value`, default 1. The first real low confirms it |
| Corroded connectors flicker (Boost 1 on 5 of 5, Grow 2 of 10 after cleaning) | A 10-minute window decides: Low at ≥ 80 % low, OK at ≤ 20 %, hold between. More than 20 changes in an hour raises **Sensor Fault** and holds the level |
| An unplugged or dead sensor reads a steady 0 = ok, in farmhand too | **Refilled** buttons and a silent-sensor alert: no Low and no Refilled for 21 days → *"check the sensor and its connector"*, weekly |
| Grow's plug is broken by corrosion | Nutrient B reads OK whatever its level until the plug is replaced. Check it by eye |

**Alerts are timed by the bridge**, not by Home Assistant: Low every 12 h while
low, Sensor Fault at most daily, silent sensor weekly. The timers are retained
on the broker, so a restart neither repeats nor forgets an alert, and the tank
names come from the app's options. `23-dosing-tank-alert` only delivers them
through `script.farm_alerts`. None is critical, and there is no alert for
*unavailable* (module missing for more than 60 s).

Everything publishes on change only: nothing reaches the recorder while the
tanks sit full. The full behaviour and every option are in
`greenery-bridge/DOCS.md`, *Dosing tank levels*.

### Acceptance test — on the farm

1. With every tank full, for 30 minutes: four **OK** under Environmental,
   matching farmhand. No alerts.
2. Restart the app: no alerts.
3. **Polarity.** The next time farmhand shows a tank low, Home Assistant shows
   the same tank **Problem** within about 10 minutes and sends one alert. If
   it shows the opposite of farmhand, set `dosing_level_low_value: 0` and
   retest.
4. Press a **Refilled** button: that tank's Days Since Refill goes to 0.
5. Recorder: under 10 rows an hour for all dosing tank entities combined. In
   **History** they change only when a tank goes low or is refilled, a sensor
   faults, or a day passes on Days Since Refill.

## farmhand reply stream (/farm-monitoring)

`/farm-control` answers `Control message received!` to everything, so it can
never confirm a read. The real answers arrive on farmhand's second SSE stream,
`/farm-monitoring`, as JSON events (verified 2026-10-15 for
`read_settings_files`, `get_farm_info`, `get_version`, `get_current_mode` and
`read_modules`):

```
{"response_to": "get_version", "response": "2.0.26", "error_message": "",
 "controller_id": "...", "request_id": "..."}
```

The bridge keeps this stream open beside `/farm-data`, with the same
reconnect behaviour, and logs any non-empty `error_message`. At start and every
5 minutes it sends `get_farm_info` and `get_version` — read-only, and both sent
by farmhand's own page on every load — and keeps:

- the Hub's live settings, behind the **24-hour settings switches**
- the version, as `sensor.greenery_s_farm_farmhand_version` (diagnostic).
  `22-farmhand-version-changed` alerts when it changes: Task Mode, the pump
  switches, Cool-Down and the 24-hour switches all use unsupported commands,
  so re-test them after every farmhand update.

To see the raw events, set `FARM_LOG_MONITORING=1` (app option **Log
farm-monitoring stream**) and restart: the first 20 events are logged at INFO.

## Cameras (latest photo only)

The latest photo from each of the farm's six cameras, like the farmhand mobile
app: **no photo history and nothing written to disk**. The owner decided
against storing photos (2026-10-29).

| Camera entity | Photo Time sensor (diagnostic) |
|---|---|
| `camera.greenery_s_farm_nursery_top_camera` | `sensor.greenery_s_farm_nursery_top_photo_time` |
| `camera.greenery_s_farm_nursery_bottom_camera` | `sensor.greenery_s_farm_nursery_bottom_photo_time` |
| `camera.greenery_s_farm_cultivation_left_camera` | `sensor.greenery_s_farm_cultivation_left_photo_time` |
| `camera.greenery_s_farm_cultivation_left_middle_camera` | `sensor.greenery_s_farm_cultivation_left_middle_photo_time` |
| `camera.greenery_s_farm_cultivation_right_middle_camera` | `sensor.greenery_s_farm_cultivation_right_middle_photo_time` |
| `camera.greenery_s_farm_cultivation_right_camera` | `sensor.greenery_s_farm_cultivation_right_photo_time` |

Plus `binary_sensor.greenery_s_farm_camera_photos_stale`, which drives
`24-camera-stale`.

### How farmhand delivers photos

Verified 2026-10-16 with `tools/farmhand/dump_cameras.py`; the captures are in
`tests/fixtures/farmhand/`. `POST {"command":"get_last_images_local_only"}`
gets `SUCCESS` on `/farm-monitoring`, then one event per camera, about 2 s
apart, on farmhand's third stream, `/farm-images`:

```
{"camera": "E45F01CD2C40", "image": "<base64 JPEG>", "last_update": 1792160573.88}
```

A photo is a 1296x730 JPEG, 550-1050 KB (1.0-1.4 M base64 characters).
farmhand takes one per camera every `image_cycle_time` (10800 s, 3 hours).
The request is read-only, and farmhand's own page sends it on every load.

### What the bridge does

- Keeps `/farm-images` open beside the other two streams, with the same
  reconnect behaviour, and sends `get_last_images_local_only` at start and
  every 30 minutes — so a new photo shows within 30 minutes of being taken.
- Names each camera from `get_farm_info`'s `camera_aliases`
  (`nursery_top` → module `E45F01CD2C40`, "Nursery Top"). Its alias decides
  the unique_id (`greenery_s_camera_nursery_top`) and topics
  (`farm/camera/nursery_top/image`), so a replaced camera module keeps its
  entity. Until `camera_aliases` arrives it uses `CAMERA_FALLBACK`, this
  farm's table; a camera in neither is logged once and not published.
- Publishes a photo **only when its `last_update` is new**: the base64 JPEG,
  retained, to the camera's MQTT camera entity (`image_encoding: b64`), then
  its time to the Photo Time sensor. The broker therefore holds exactly one
  photo per camera, and HA shows it again after a restart. At start the bridge
  reads its retained Photo Times back, so a restart does not send the same six
  photos (about 8 MB) again.
- Never puts the photo in a sensor state or attribute — the recorder would
  keep every one.
- Logs, once per start, whether a photo ever arrives **without** a request:
  `Camera photo arrived WITHOUT a request …`, or after a full photo cycle
  `No camera photo arrived without a request … farmhand sends photos only when
  asked`. farmhand's own page asks on every load and every listener gets the
  photos, so a farmhand page left open counts as a request.

**Camera Photos Stale** is ON while any camera's latest photo is older than
2.5 photo cycles — 7.5 h at farmhand's `image_cycle_time`, read live from
`get_farm_info`. Its `stale_cameras` attribute names them. A camera farmhand
lists but that never sends a photo counts from the bridge's start.
`24-camera-stale` alerts (not critical) on OFF → ON, at most once per 24 h.

**Recorder.** Photo Time and Camera Photos Stale change only with a new photo
or a stale camera — a few rows a day. HA may also write a row per camera every
5 minutes when it rotates the camera's access token; DOCS.md, *Cameras →
Recorder*, has the check and the `exclude` that removes them.

### Dashboard

`dashboard-cameras.yaml`: six photos in a two-column grid (phone width), in the
order above, state text hidden; a tap opens the photo full size. Under it, a
row that appears only while Camera Photos Stale is on. It goes on its own view
(URL `farm-cameras`) so the main view never loads six photos; the **Cameras**
button at the bottom of `dashboard-controls.yaml` opens it.

### Acceptance test

1. The Cameras view shows six current photos, each the area its name says.
   Each Photo Time matches farmhand.
2. After the next 3-hour photo cycle, new photos appear within 30 minutes.
3. Restart the app, then HA: the photos still show, with no duplicate
   entities.
4. `/share` and `/media` have no new camera folders.
5. Camera Photos Stale: **stopping the bridge does not test it** — farmhand
   keeps taking photos meanwhile, and the bridge fetches the latest at
   restart. Test the alert from Developer Tools → States (set the sensor to
   `on`, from `off`): one alert, and none on a second try within 24 h. The
   threshold itself is covered by `pytest -q tests -k stale`, including a
   lowered `image_cycle_time`; for a live test, unplug one camera for 8 h.
6. Recorder: the check in DOCS.md, *Cameras → Recorder*.
7. HA's system monitor shows no lasting rise in CPU or memory.

## Single point of failure: CloudGate

**The farm's only internet path is CloudGate cellular, and Home Assistant
shares it.**

CloudGate freezes periodically. When it does:

- The farm keeps running. The Hub is local and the recipe executes locally.
- The HA bridge keeps working. It reads `192.168.200.200` over the LAN.
- Every automation keeps firing correctly.
- **And not one notification leaves the building.**

Everything reads green while nothing reaches the phone. This is not
theoretical: it is how the farm reached 81 °F undetected, discovered only
because the farmhand data looked stale and the owner drove out to check.

Understand this clearly: **the alerting in this repo does not survive a
CloudGate freeze on its own.** Automations 14 and 15 shorten the window and
recover the lost alert, but they do not remove the dependency.

### Automation 14 — watchdog

Ping two targets on different networks. Both unreachable for 10 minutes →
power-cycle CloudGate → wait up to 6 minutes for recovery → report.

Cycles at most once per 30 minutes. If two cycles do not fix it, the outage is
upstream — carrier, or no signal — and further cycling only wears the hardware.
In that case it sends a critical alert (which will only arrive once
connectivity returns) stating plainly that the farm is unmonitored from
outside.

**The plug must be LOCALLY controlled** — Shelly, Zigbee, or ESPHome. A
cloud-dependent plug (Tuya, most Kasa configurations) cannot be reached when
the internet is down, which is the only time this automation runs. Getting this
wrong produces a watchdog that works in testing and fails in production.

### Automation 15 — missed alert replay

Push notifications sent during an outage are **lost, not queued**. An alarm
that fires during a CloudGate freeze never arrives.

`farm-alerts-script.yaml` records any alert it could not deliver
(`input_boolean.farm_alert_missed` + `input_text.farm_missed_alert`), and
automation 15 replays it once connectivity returns — flagged as possibly stale.

An outage now delays an alert instead of erasing it. This is what turns the
persistent-notification fallback from a passive log into a delivery queue.

### Setup

See `watchdog-helpers.yaml` for the two helpers and the Ping integration
config. All UI, no `configuration.yaml`.

### The structural fix is a second uplink

The watchdog shrinks the blind window from *"however long until you notice"*
to roughly 12–16 minutes. That is a large improvement and it is not a fix.

While CloudGate is down there is still no path out of the container. A
temperature excursion in that window is delayed, not caught.

A second, independent uplink — different carrier, or any wired service
available at the site — is the only thing that removes the single point of
failure. Give Home Assistant its own path to the internet and the farm's
monitoring stops depending on the same box whose freezes caused the problem.

For a facility holding perishable inventory, that is worth more than any
sensor on the roadmap.

## Module offline detection

The farm's internal communication runs over five modules. Freight Farms ships a
built-in alert — *"Module has been offline for 5 minutes"* — but it is delivered
through the farmhand **cloud**, which is exactly what goes silent when CloudGate
freezes. `farm_bridge.py` detects it locally instead.

Urgency differs sharply by module, so they are published separately rather than
lumped into one sensor. From the Freight Farms module troubleshooting article:

| Module | Device | Offline means | Severity |
|---|---|---|---|
| Environmental | `94E68607D090` | *"the farm will refuse to turn on the HVAC system or the LED lights"* | **CRITICAL** |
| Output | `244CAB0FC00C` | *"none of your equipment will receive power"* | **CRITICAL** |
| Cultivation Dosing | `94E68607D218` | *"no water supply dosing occurs"* | HIGH |
| Nursery Dosing | `244CAB0FD624` | same, nursery side | HIGH |
| Input | `8C4B14715024` | *"will not pose an immediate threat to your crops"* — data lapse only | INFO |

### Two detection signals

A module counts as offline if **either** holds:

- its own `connected` flag reports false
- `last_update` is older than 300 s — farmhand's own 5-minute definition

The staleness half is the one that matters. **A module can keep claiming
`connected` while its data stops advancing**, and the bridge would go on
republishing its last known values indefinitely. pH, EC, temperature and relay
states would all read plausibly and mean nothing.

This is the same trap as a stopped recirc pump: the readings do not go blank,
they go *wrong*. Every alarm downstream of an offline module is unreliable
until it clears, which is why automations 16 and 17 say so explicitly in the
notification text.

### First response

Per Freight Farms, before entering the farm check the **high-voltage electrical
box on the exterior back** for tripped breakers. A dosing module offline is most
often a **leak in the dosing cabinet**, especially at sensor ports after a recent
hydro sensor calibration — water reaching a power block trips the whole branch on
the right side of the nursery station, which is why an input module alert often
arrives alongside it.

### Coverage against farmhand's built-in alerts

| Freight Farms built-in alert | Covered by |
|---|---|
| Farm has been in Task Mode for 6 hours | `12-task-mode-left-on` |
| Farm has been offline for 1 hour | `01-bridge-offline` + `14-cloudgate-watchdog` |
| High Humidity | `06-humidity-high` |
| High or Low Air Temperature | `05-air-temp-high` + `18-air-temp-low` |
| HVAC Cooling has been running for 24 hours | `19-hvac-cooling-stuck` |
| Left/Right Send Pump — Insufficient Pressure | `20-send-pump-pressure` |
| Low CO2 | `04-co2-below-minimum` |
| Module has been offline for 5 minutes | `16` + `17` |

Full parity, plus the alarms farmhand does not ship: water temp, EC low, CO2
high, tank depth, recirc pump stopped, and relay mismatch.

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
5. Repeat for the remaining nineteen files

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
  replicate those interlocks before going live. The only exception so far is
  the hand-operated pump switches, Task Mode only — no automation drives them
- Mobile-optimized dashboard layout — current layout targets desktop width
