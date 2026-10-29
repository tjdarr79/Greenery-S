# Changelog

## 1.4.0

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
  New automation `23-camera-stale`, not critical, at most once every 24 h.
- New `dashboard-cameras.yaml` for a Cameras view of its own, and a
  **Cameras** button at the bottom of `dashboard-controls.yaml`. Re-paste the
  controls card (or its rendered copy) to get the button.
- After an hour of running, do the recorder check in DOCS.md, *Cameras →
  Recorder*.

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
