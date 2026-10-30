# Changelog

## 1.5.1

Duplicate bridge protection. Safe to upgrade: no entity ID, unique_id or MQTT
topic changes, no new options. **Update before releasing to `stable`.**

On 3 Corners Farm the retired Windows bridge, still started by Task Scheduler
on the farm PC, ran alongside this app. Both connected to Mosquitto as
`farm-bridge`; the broker allows one connection per client ID, so each kicked
the other off about once a second, and every kick took every entity
unavailable. The database grew to 16.5 GB at ~10 million rows a day, and Home
Assistant slowed down (Cool-Down with it).

- **A client ID of its own** for every start:
  `greenery-bridge-<farm>-<6 hex>`, logged on connect. Two bridges can no
  longer kick each other off.
- **Duplicate Bridge Detected** (diagnostic, problem), on when another bridge
  announces itself on `farm/bridge/instance`, when another bridge's Last Will
  sets the farm offline while this one is connected (the app sets it straight
  back online, so entities do not stay unavailable), when a bridge older than
  1.5.1 connects (it sets the farm online and never announces itself; the
  sensor then stays on until it stops), or after more than 5 unexpected
  disconnects in 60 s. Attributes `other_id`, `other_host`,
  `other_version`, `reason`. Clears after 10 quiet minutes. When one of the two
  is the app, only the app writes it, so `other_host` names the other computer.
  Every bridge from 1.5.1 announces itself.
- New automation `25-duplicate-bridge`: a **critical** alert naming the other
  computer. Paste it.
- Legacy Windows installer: a warning not to run it alongside the app, and
  `UNINSTALL-FARM-BRIDGE.bat` now also removes the `FarmBridge` task from the
  manual guide (and any task that runs `farm_bridge.py`), stops the bridge
  whatever its Python process is called, and lists what it did.
- DOCS.md: Troubleshooting, *Every entity keeps going unavailable / database
  growing fast*, and a *Database health check*: rows per entity in the last
  hour. README: *No other bridge on this farm's network* in New install.

## 1.5.0

Cameras, latest photo only. Safe to upgrade: no existing entity ID changes and
no new options.

- **Six cameras** — Nursery Top, Nursery Bottom, Cultivation Left, Cultivation
  Left Middle, Cultivation Right Middle, Cultivation Right — each showing its
  latest photo, named from farmhand. Like the farmhand mobile app there is no
  history: nothing is written to disk, and the broker keeps one photo per
  camera so Home Assistant shows it after a restart. The app asks farmhand for
  the latest photos at start and every 30 minutes (read-only; farmhand's own
  page sends the same request) and publishes a photo only when it is new. See
  DOCS.md, *Cameras*.
- A **Photo Time** sensor per camera (diagnostic), and **Camera Photos
  Stale**: on while any camera's photo is over 2.5 photo cycles (7.5 h) old.
  New automation `24-camera-stale`, not critical, at most once every 24 h.
- New `dashboard-cameras.yaml` for a Cameras view of its own, and a
  **Cameras** button at the bottom of `dashboard-controls.yaml`. Re-paste the
  controls card (or its rendered copy) to get the button.
- After an hour of running, do the recorder check in DOCS.md, *Cameras →
  Recorder*.

## 1.4.0

Dosing tank levels. Safe to upgrade: no existing entity ID changes. Seven new
options, all with defaults matching 3 Corners Farm.

- Four **dosing tank level** sensors, read from module `E831CDC7E680` like
  farmhand's Environmental card: **Nutrient A Level**, **Nutrient B Level**,
  **pH Down Level**, **Boost / pH Up Level** — *OK*, or *Problem* when the tank
  is low. A rolling 10-minute window decides, so a corroded connector's
  flicker never reads as low. Attribute `low_percent` for tuning.
- Per tank: **Sensor Fault** (diagnostic, more than 20 reading changes in an
  hour; the level holds while it is on), **Days Since Refill**, and a
  **Refilled** button.
- **A dead or unplugged sensor reads OK**, like a full tank, in farmhand too.
  A tank with no Low and no Refilled press in 21 days sends a reminder to
  check its sensor, weekly. Press Refilled after every refill.
- Alerts timed by the app, delivered by the new automation
  `23-dosing-tank-alert`: tank low (every 12 h while low), sensor unstable (at
  most daily), sensor silent (weekly). Their timers survive restarts. None is
  critical; no alert for *unavailable* (module missing over 60 s).
- New options: `dosing_tank_names`, `dosing_tank_inputs`,
  `dosing_tanks_enabled`, `dosing_level_low_value` (set `0` if Home Assistant
  reads the opposite of farmhand), `dosing_level_window_s`,
  `dosing_sensor_fault_changes_per_hour`, `dosing_silent_days`. See DOCS.md,
  *Dosing tank levels*.
- Published on change only: nothing reaches the recorder while the tanks sit
  full.
- Paste `23-dosing-tank-alert.yaml`, and re-paste `farm-dashboard.yaml` and
  `farm-dashboard-mobile.yaml` (or their rendered copies) for the **Dosing
  Tanks** card under Environmental. `tools/farmhand/dosing_levels.py` prints
  the raw inputs.

## 1.3.0

Cool-Down, a fourth pump switch, 24-hour settings switches and the farmhand
version. Safe to upgrade: no existing entity ID changes.

- **Start Cool-Down / End Cool-Down** buttons, for a farm running hot: Task
  Mode takes the grow lights off and relays 1 and 8 (cultivation recirc,
  nursery recirc + chiller pump) are switched back on, each step confirmed
  from the relays. Phone only, never automatic, not for cleanouts. New
  **Cool-Down Active** and **Cool-Down Fault** sensors; a pump that stops for
  60 s during a cool-down raises the fault. See DOCS.md, *Cool-Down*.
- **Nursery Recirc and Chiller Pump Switch** (relay 8), Task Mode only like
  the other three. Physically tested 2026-10-16.
- **Nursery Top 24 h**, **Nursery Bottom 24 h**, **Cultivation Watering 24 h**
  switch farmhand's own 24-hour settings, replacing the cloud app toggle. They
  show what the Hub reports and confirm every change by reading it back. Those
  six settings are the only ones the app can ever write.
- **farmhand Version** sensor (diagnostic).
- The app now keeps farmhand's reply stream (`/farm-monitoring`) open, and at
  start and every 5 minutes sends two read-only queries farmhand's own page
  sends on every load: `get_farm_info` and `get_version`. **Log farm-monitoring
  stream** now only logs it.
- Pump switches no longer go *unavailable* for a few seconds every minute when
  farmhand restarts its data stream. They keep the last known Task Mode state
  through the reconnect and go unavailable only after 30 s with no
  output-board data (or with the app offline). A press is still decided from
  the latest data at that moment, and refused if it is over 30 s old.
- New automations `21-cool-down-fault` (critical) and
  `22-farmhand-version-changed`. Re-paste `dashboard-controls.yaml` (or its
  rendered copy) for the Cool-Down, relay 8 and 24-Hour Settings cards.

## 1.2.0

Pump switches, Task Mode only. Safe to upgrade: no existing entity ID changes.

- Three new switches: **Cultivation Recirc Pump Switch**, **Left Send Pump
  Switch**, **Right Send Pump Switch**. They show *unavailable* unless the farm
  is in Task Mode, and the bridge refuses any command sent outside Task Mode.
  Each press reports `CONFIRMED` or `NOT CONFIRMED` in **Farm Control Status**,
  read back from the relay itself — farmhand's reply confirms nothing. See
  DOCS.md, *Pump switches*.
- `dashboard-controls.yaml` gains a **Pumps (Task Mode only)** card, every
  tap confirmed. Re-paste it (or its rendered copy) to get it.
- Commands left **retained** on the broker are now ignored and cleared, never
  replayed on restart. The Task Mode buttons no longer hold up other MQTT
  traffic while they verify, and two presses never run at once.
- New option **Log farm-monitoring stream**, off by default. Investigation
  only — logs farmhand's second event stream so its replies can be found.

## 1.1.0

Multi-farm identity. Safe to upgrade: an existing install keeps its device name
and every entity ID.

- New **Farm name** option. It names the Home Assistant device and decides the
  entity IDs of a fresh install (`Smith Farm` → `sensor.smith_farm_*`). It
  defaults to `Greenery S Farm`, the name every install already had. **Set it
  before the first start** — see DOCS.md, *Farm name*.
- `tools/render-farm-yaml.py` writes a farm's own copy of the automations and
  dashboards for any name other than the default.
- Configuration tab fields now have labels and descriptions.
- Alert titles start with the farm's name (`Smith Farm: Water Temp High`), from
  a new **Farm Name** helper. Re-paste `farm-alerts-script.yaml` and create the
  helper to get it; without the helper, titles are unchanged.
- Task Mode buttons now send to the configured `farm_host`/`farm_port`.
  Previously they always used `192.168.200.200`, so a farm on another address
  had working sensors but buttons that could not reach the controller.

## 1.0.0

First release.

Runs the Greenery S bridge directly on Home Assistant, replacing the separate
Windows PC entirely.

- Publishes all farm sensors, 32 relay channels, module offline detection and
  Task Mode controls over MQTT with auto-discovery
- Takes broker credentials from Supervisor automatically - no host, username or
  password to type
- Manual broker override for anyone not using the Mosquitto add-on
- Warns at startup if the farm controller is unreachable, so that failure is
  not mistaken for an MQTT problem
