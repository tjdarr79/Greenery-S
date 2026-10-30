# Greenery S Farm Bridge

Reads your Freight Farms Greenery S local Farmhand endpoint and publishes
everything to Home Assistant over MQTT: sensors, all 32 relay channels, module
health, one-tap Task Mode controls, pump switches that work only in Task Mode,
a one-tap Cool-Down, switches for farmhand's 24-hour settings, the four
dosing tank levels with low and sensor-fault alerts, and the latest photo
from each of the farm's six cameras.

No farmhand cloud subscription is needed for any of it.

## Install

1. **Settings → Apps** (Add-ons in older releases) **→ Store → ⋮ → Repositories**
2. Add the repository for your channel — see [Release channel](#release-channel):
   - customer farm: `https://github.com/tjdarr79/Greenery-S#stable`
   - 3 Corners Farm (development): `https://github.com/tjdarr79/Greenery-S`
3. Install **Greenery S Farm Bridge**
4. **Configuration** tab → set **Farm name** → **Save**. Do this *before* the
   first start — see [Farm name](#farm-name) below.
5. Press **Start**

That is usually the whole setup. If the Mosquitto broker add-on is installed
and running, this add-on picks up the broker address and credentials from
Home Assistant automatically — there is nothing to type.

## Configuration

| Option | Default | |
|---|---|---|
| `farm_name` | `Greenery S Farm` | Names this farm in HA and decides its entity IDs. **Set before the first start** |
| `farm_host` | `192.168.200.200` | The Farmhand hub controller's IP |
| `farm_port` | `3001` | Local Farmhand port |
| `log_level` | `info` | Raise to `debug` when troubleshooting |
| `log_farm_monitoring` | `false` | Investigation only — see [farmhand reply stream](#farmhand-reply-stream) |
| `mqtt_host` | *(blank)* | Only for an external broker |
| `mqtt_port` | `0` | Blank/0 means 1883 |
| `mqtt_user` | *(blank)* | Only for an external broker |
| `mqtt_password` | *(blank)* | Only for an external broker |
| `dosing_tank_names` | `Nutrient A`, `Nutrient B`, `pH Down`, `Boost / pH Up` | One per tank, farmhand's order. **Set before the first start** — see [Dosing tank levels](#dosing-tank-levels) |
| `dosing_tank_inputs` | `digital_1` … `digital_4` | The input each tank's sensor is wired to. Swap two to fix a swapped wire |
| `dosing_tanks_enabled` | `true` ×4 | A tank set `false` gets no entities and no alerts |
| `dosing_level_low_value` | `1` | The raw value that means low. `0` if HA reads backwards |
| `dosing_level_window_s` | `600` | Readings window the level is decided over |
| `dosing_sensor_fault_changes_per_hour` | `20` | More changes than this in an hour → Sensor Fault |
| `dosing_silent_days` | `21` | No Low and no Refilled press this long → silent-sensor reminder |

**Leave every `mqtt_*` field blank** unless you run a broker outside Home
Assistant. Filling them in overrides the automatic detection.

Only `farm_name` always needs attention. `farm_host` needs it only if your farm
controller does not use the stock address — the Task Mode buttons follow it
automatically.

## Farm name

`farm_name` tells farms apart. One person responsible for several farms sees
it on the device, on every entity's friendly name, and — through the Farm Name
helper, see `README.md` — at the start of every alert title, so a phone banner
says which farm paged them without opening the app.

The alert-title prefix comes from the **Farm Name** helper
(`input_text.farm_name`), not from this option — the alert script runs inside
Home Assistant and cannot read the app's configuration. Create the helper and
set its value once per install, as part of onboarding: see
`watchdog-helpers.yaml`, and **New install** step 3 in `README.md`.

> **Known rough edge:** the helper and `farm_name` are two separate settings,
> and nothing keeps them in step. Type the same name in both. If they differ,
> alerts carry one name and the device and dashboards another. If the helper
> is never created or left blank, alerts simply have no prefix — nothing fails.

It becomes the device name, and Home Assistant builds each entity ID from the
device name the first time it sees the entity:

| `farm_name` | Device | Example entity ID |
|---|---|---|
| `Greenery S Farm` *(default)* | Greenery S Farm | `sensor.greenery_s_farm_cultivation_ph` |
| `Smith Farm` | Smith Farm | `sensor.smith_farm_cultivation_ph` |

Every farm has its own Home Assistant, so two farms may share a name without
colliding — but give each one a distinct name anyway, or the alerts cannot be
told apart.

**Set it before the first start.** An install upgraded from 1.0.0 is given the
default, which is the name it already had, so nothing about it changes.

### Any name other than the default needs rendered YAML

The repository's automations and dashboards name their entities literally —
`sensor.greenery_s_farm_cultivation_ph`. On a farm with any other name those
entities do not exist, and an automation triggered by an entity that does not
exist **never fires and never reports an error**. The notification test cannot
catch it, because that tests the script, not the triggers.

So a farm with its own name pastes rendered copies, never the repository
originals:

```
python tools/render-farm-yaml.py "Smith Farm"
```

It writes every automation, dashboard and the controls card with the farm's
entity IDs into `rendered/smith_farm/`. The app's **Log** tab prints the exact
command for the name you configured.

> **⚠ Warning — do not change `farm_name` casually after entities exist.**
>
> Home Assistant keeps an entity's ID once it has been created. Changing
> `farm_name` later renames the device and every friendly name, but leaves the
> IDs under the old name — the farm now says one name and answers to another.
>
> Anything that brings the IDs in line — accepting Home Assistant's offer to
> rename entity IDs, deleting and re-adding the device, a rebuild from scratch
> — gives every entity a **new** ID. Every automation, dashboard card and
> script that uses the old ID then points at nothing: **automations stop firing
> without any error**, and recorded history and long-term statistics stay
> behind on the old ID, split from anything recorded afterwards.
>
> If a rename is unavoidable: re-render the YAML with the new name, re-paste
> every automation and dashboard, set the Farm Name helper to match, and accept
> that history starts over at the new ID.

## Release channel

Customer farms install from the `stable` branch; 3 Corners Farm installs from
`main` and proves every change before it reaches `stable`. The branch is
chosen by the `#stable` on the end of the repository URL.

[One-click add, stable channel](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftjdarr79%2FGreenery-S%23stable)

**Confirmed from source — not yet on a live install.** Supervisor accepts
`<url>#<branch>` for an app repository: it validates against
`^(?P<url>[^#\s]+)(?:#(?P<branch>[\w\-./]+))?$` and clones that branch
(`supervisor/validate.py` `RE_REPOSITORY`, `supervisor/store/git.py`). The
one-click link hands `repository_url` to the Apps store, which adds it exactly
as given, so `#stable` survives (`%23` in the link). Neither has yet been
exercised on real hardware from this repository.

**At the first customer install, confirm it** before relying on it:

1. Settings → Apps → Store → ⋮ → Repositories lists the URL **with** `#stable`.
2. The app's version matches `version:` in `greenery-bridge/config.yaml` on the
   `stable` branch on GitHub — not the one on `main`, whenever they differ.

**If either check fails:** remove the repository before installing the app
(Supervisor refuses to remove one while its app is installed) and fall back to
a separate repository that holds only releases — for example
`tjdarr79/Greenery-S-stable`, updated by pushing `stable` to it. Customers add
that URL instead, with no `#`.

**Choose once.** Moving an installed farm to the other channel means
uninstalling and reinstalling the app.

**Updating a customer farm:** leave **Create backup** on in the update dialog.
Supervisor cannot install an older version, so that backup is the fastest way
back from a bad release.

## Checking it worked

**Settings → Devices & Services → MQTT → your farm name** (`Greenery S Farm`
unless you changed it).

You should see roughly 100 entities. Note the device lives *inside* the MQTT
card — it is not listed at the top level of Devices & Services, which is easy
to miss.

The add-on **Log** tab should show `Farm name: …` with the entity ID prefix,
then `MQTT connected: Connected successfully (client ID greenery-bridge-…)`,
then a steady stream of published states. **Duplicate Bridge Detected**, under
the device's Diagnostic entities, should be **off** — see Troubleshooting.

## Pump switches (Task Mode only)

Four switches turn single pumps on and off from Home Assistant:

| Switch | Relay channel |
|---|---|
| Cultivation Recirc Pump Switch | 1 |
| Left Send Pump Switch | 2 |
| Right Send Pump Switch | 3 |
| Nursery Recirc and Chiller Pump Switch | 8 — also the chiller pump: off stops the chiller's flow |

**They only work in Task Mode.** Outside it they show *unavailable*, and a
command sent to one anyway — by an automation, or published by hand — is
refused: **Farm Control Status** reads `REFUSED - farm not in Task Mode` and
nothing reaches the farm. The bridge checks Task Mode again at the moment of
every command, from output-board data no older than 30 seconds.

They do not flicker when farmhand restarts its data stream (about once a
minute): they go unavailable only after 30 seconds with no output-board data,
or when the app itself is offline.

**farmhand's reply proves nothing.** It answers `Control message received!` to
every command, including ones it ignores. So the bridge watches the relay's own
state in the farm's data stream and reports `CONFIRMED in 2.1s`, or
`NOT CONFIRMED - relay still reports OFF` after 10 seconds.

A switch left on keeps its pump running until someone turns it off. In Task
Mode farmhand's recipe is suspended, and with it its automatic protections —
stay with the farm while a pump you started is running.

No command is sent when the app starts or restarts — only the read-only
queries described under [farmhand reply stream](#farmhand-reply-stream) and
[Cameras](#cameras) — and
a command left *retained* on the broker is ignored and cleared rather than
replayed.

**Adding a channel** is a code change, in this order: switch it on and off from
the farmhand UI in Task Mode and watch the equipment respond; then add the
channel number to `MANUAL_CONTROL_CHANNELS` in `farm_bridge.py`; then, in Task
Mode, switch it on and off from Home Assistant, watch the equipment, and see
`CONFIRMED` both ways before relying on it. Never channel 23 — the bridge
refuses to start with it listed.

The **Pumps (Task Mode only)** card is in `dashboard-controls.yaml`; every tap
asks for confirmation first.

## Cool-Down (phone only)

For a farm running hot in the day. **Start Cool-Down** puts the farm in Task
Mode — grow lights off, recipe suspended — then switches relays 1 and 8 back
on: cultivation recirc, and nursery recirc with the chiller pump, so water
keeps moving through the chiller. **End Cool-Down** exits Task Mode and the
recipe resumes.

- **Phone only, never automatic.** Nothing starts or ends a cool-down but a
  person pressing the button.
- **Not for cleanouts.** Cleanouts are run from farmhand Local and never use
  these buttons.
- **Every step is confirmed from the relays**, not from farmhand's reply.
  **Farm Control Status** ends in `Cool-Down: ACTIVE - lights off, recirc and
  chiller pump running`, or says what failed.
- **Cool-Down Fault** turns on if either pump cannot be confirmed running — at
  Start (after one retry), after End, or when one reports OFF for 60 seconds
  during a cool-down — and `21-cool-down-fault` sends a critical alert: in Task
  Mode the chiller stays powered and may run with no water flow. On a fault at
  Start the farm is left in Task Mode for you to decide.
- **Cool-Down Active** goes off whenever the farm leaves Task Mode, by any
  route. After an app restart it comes back on only if the farm is still in
  Task Mode; the restart sends no command.

The **Cool-Down** card is at the top of `dashboard-controls.yaml`, with a
confirmation on both buttons.

## 24-hour settings

Three switches — **Nursery Top 24 h**, **Nursery Bottom 24 h**, **Cultivation
Watering 24 h** — write farmhand's own 24-hour settings, which keep a nursery
trough lit, or cultivation watering cycling, through the recipe's night. They
replace the farmhand cloud app's toggle once the subscription ends. They work
in auto; Task Mode does not gate them.

- Each writes its pair of flags with the command farmhand Local uses when it
  saves a recipe card. **Those six flags are the only settings the app can
  ever write**; it refuses and logs anything else.
- Each shows **what the Hub reports**, read every 5 minutes and after every
  change — never what was asked. If a pair disagrees (red on, blue off) the
  switch shows off and Farm Control Status reads `MIXED - red/blue differ,
  check farmhand`.
- A change reports `CONFIRMED` or `NOT CONFIRMED - farm reports …` within 10
  seconds.

The **24-Hour Settings** card in `dashboard-controls.yaml` confirms every tap.
`tools/farmhand/set_24hr.py` does the same from a PC if Home Assistant is down.

## farmhand reply stream

farmhand answers every command with `Control message received!`. The real
answers arrive on its second stream, `/farm-monitoring`, which the app keeps
open beside the data stream. At start and every 5 minutes it sends the two
read-only queries farmhand's own page sends on every load, `get_farm_info` and
`get_version`, for the 24-hour switches and the **farmhand Version** sensor.
When the version changes, `22-farmhand-version-changed` reminds you to re-test
Task Mode, the pump switches, Cool-Down and the 24-hour switches — all of them
use commands Freight Farms never documented.

`log_farm_monitoring` logs the first 20 events of that stream after each start,
for investigating a new reply. Leave it off otherwise.

## Dosing tank levels

Each 1-gallon dosing tank has an optical level sensor that trips when the tank
is almost empty (Owner's Manual: *Dosing Tank Level Sensors*). farmhand shows
them on its Environmental card as *ok* or low. The app reads the same four
inputs, module `E831CDC7E680`, and adds per tank:

| Entity | Shows |
|---|---|
| **Nutrient A Level** (etc.) | **OK**, or **Problem** (red) when the tank is low. Attribute `low_percent` |
| **Nutrient A Sensor Fault** | Diagnostic. On while the sensor flickers |
| **Nutrient A Days Since Refill** | Days since the last refill: a **Refilled** press, or the sensor going low then ok |
| **Nutrient A Refilled** | Button. Press it after refilling the tank |

| Order | Tank | Owner's product | Input |
|---|---|---|---|
| 1 | Nutrient A | Form | `digital_1` |
| 2 | Nutrient B | Grow | `digital_2` |
| 3 | pH Down | pH Down | `digital_3` |
| 4 | Boost / pH Up | Boost | `digital_4` |

The **Dosing Tanks** card sits under **Environmental** in `farm-dashboard.yaml`
and `farm-dashboard-mobile.yaml`, in farmhand's order, with a row of four
**Refilled** buttons, each confirmed, underneath. A Sensor Fault row appears
only while that fault is on. Home Assistant labels the problem class *OK* /
*Problem*; it has no "LOW" label.

### A dead sensor reads OK

An unplugged, broken or dead sensor reads 0, which is **OK**, exactly like a
full tank, and farmhand shows it the same way. Nothing in the data tells the
two apart. Owner test, 2026-10-29: with the Grow sensor unplugged, `digital_2`
read a steady 0 and farmhand showed Grow *ok*.

So the app watches for silence instead. A tank that has not reported low, and
has had no Refilled press, in `dosing_silent_days` (21 days; a tank normally
lasts about a week) sends a low-priority reminder: *"Nutrient A hasn't
reported low in 21 days. Check the sensor and its connector."* It repeats
weekly until the clock restarts.

**Press Refilled after every refill.** That restarts the tank's clock and sets
Days Since Refill to 0. A sensor seen going low and then ok again restarts it
too. Without the presses, a tank whose sensor never trips because it is
always topped up early reads exactly like a dead sensor.

> **3 Corners Farm, Nutrient B (Grow):** the plug is broken by corrosion and
> reads ok whatever the level until it is replaced (Growcer, parts or
> service). Until then, check that tank by eye.

### Flicker and Sensor Fault

Corroded connectors make the inputs flicker. In the owner's tests with every
tank full, Grow and Boost flickered to 1 (Boost on 5 of 5 readings in one
run). After the connectors were cleaned, Grow still read 1 on 2 of 10. So no
single reading decides anything:

- **Level** is decided over a rolling window of readings,
  `dosing_level_window_s` (600 s). It goes **Low** when 80% or more of the
  readings in a full window read low, and **OK** at 20% or less. In between,
  it holds. It is never decided from a partial window: after the app starts,
  or after the module comes back, the Level keeps its last state (or shows
  *unknown* on a first install) until a full window has passed. Attribute
  `low_percent` is the current share, for tuning.
- **Sensor Fault** turns on when the reading changes more than
  `dosing_sensor_fault_changes_per_hour` (20) times within an hour. It turns
  off after an hour with 5 changes or fewer. While it is on, the Level holds
  and sends no low alert. Clean or replace the connector.

### If Home Assistant reads backwards

Low is **assumed** to read 1: no real low tank had been seen when this was
built. The first time farmhand shows a tank low, Home Assistant must show the
same tank **Problem** within about 10 minutes and send one alert. If Home
Assistant shows the opposite of farmhand (for example every full tank showing
Problem), set `dosing_level_low_value` to `0`, save, restart the app and
check again.

A tank showing another tank's state is a swapped wire: swap the two entries
in `dosing_tank_inputs`. `tools/farmhand/dosing_levels.py` prints the raw
inputs to compare with farmhand.

### Alerts

The app decides when to alert and `23-dosing-tank-alert` delivers each alert
through the alert script, so the titles carry the farm name.

| Event | Message | Repeat |
|---|---|---|
| Tank goes Low | *Smith Farm: Nutrient A tank low. Refill it.* | every 12 h while low |
| Sensor Fault on | *Smith Farm: Nutrient B tank sensor unstable. Check its connector.* | at most every 24 h |
| Silent sensor | *Smith Farm: Nutrient A hasn't reported low in 21 days. …* | weekly |

None is critical. The alert script has two levels, critical and normal, so the
spec's *low* priority goes out as a normal push. It differs only in how often
it repeats. The repeat timers are kept on the broker (retained,
`farm/dosing_tank_N/memory`), so restarting the app or Home Assistant neither
repeats an alert nor forgets one.

**No alert for unavailable.** If module `E831CDC7E680` is missing from the
data for more than 60 s, the Level and Sensor Fault entities go
*unavailable*, never Low. A routine data-stream reconnect, a few seconds about
once a minute, changes nothing. This module is **not** among the five that
module-offline detection covers, so the dashboard showing *unavailable* is the
only sign.

### Names, entities and the recorder

The names set the entity names. Like `farm_name`, they decide the entity IDs
the first time Home Assistant sees them (`Nutrient A Level` gives
`binary_sensor.greenery_s_farm_nutrient_a_level`), and the dashboards name
the default IDs. Set `dosing_tank_names` before the first start, or leave it.
Unique IDs and MQTT topics follow the tank's position (1-4), never its name,
so a later rename keeps each entity and its history. A tank turned off in
`dosing_tanks_enabled` loses its entities: delete its rows and its Refilled
button from the Dosing Tanks card.

Every state is published only when it changes, so the recorder stores only
changes: none while the tanks sit full, and well under 10 rows an hour for
all sixteen entities otherwise.

## Cameras

The latest photo from each of the farm's six cameras: **Nursery Top**,
**Nursery Bottom**, **Cultivation Left**, **Cultivation Left Middle**,
**Cultivation Right Middle** and **Cultivation Right**. The names are
farmhand's own.

- **Only the latest photo is kept**, like the farmhand mobile app. There is
  no photo history, nothing is written to disk, and there is no storage to set
  up. The broker keeps one photo per camera (retained, about 1 MB each, each
  replaced by the next), so Home Assistant shows them again after a restart.
- **farmhand takes a photo every 3 hours.** The app asks farmhand for the
  latest photos at start and every 30 minutes — the read-only request
  farmhand's own page sends on every load — so a new photo shows within 30
  minutes of being taken. A photo is sent to Home Assistant only when it is
  new.
- Each camera has a **Photo Time** sensor, under the device's Diagnostic
  section: when its photo was taken. It should match farmhand.
- **Camera Photos Stale** turns on when any camera's latest photo is older
  than 2.5 photo cycles — 7.5 hours at farmhand's 3-hour cycle, which the app
  reads from farmhand. It lists those cameras, and `24-camera-stale` sends one
  alert (not critical), at most once every 24 hours.

**Dashboard.** Add a view to your dashboard titled *Cameras*, URL
`farm-cameras`, and paste `dashboard-cameras.yaml` into it as a Manual card.
The **Cameras** button at the bottom of `dashboard-controls.yaml` opens it. On
its own view the photos never slow the main view on a phone. While the view is
open, Home Assistant re-reads each photo every 10 seconds (its normal camera
behaviour); nothing is fetched once it is closed.

The **Log** tab shows `New photo: Nursery Top, taken …` for each new photo.
Once per start it also says whether farmhand ever sends a photo without being
asked — `Camera photo arrived WITHOUT a request`, or after a full photo cycle
`No camera photo arrived without a request`.

### Recorder

A photo never goes into a sensor's state or attributes, so the database never
stores one. **Photo Time** and **Camera Photos Stale** are recorded: a few
rows a day.

The camera entities should add almost nothing either: Home Assistant does not
record a camera's access token. It does rotate that token every 5 minutes,
though, and each rotation may still write a row — up to 72 rows an hour for
six cameras. **Check after an hour of running:** Developer Tools → Template,

```
{{ states.camera.greenery_s_farm_nursery_top_camera.last_updated }}
```

then again 10 minutes later. If it moved while that camera's Photo Time did
not, each move is a database row: exclude cameras from the recorder in
`configuration.yaml`, then restart Home Assistant:

```yaml
recorder:
  purge_keep_days: 90
  exclude:
    domains:
      - camera
```

`purge_keep_days: 90` is 3 Corners Farm's existing setting — keep whatever
your `recorder:` block already has and add only the `exclude:` lines. Without
a `recorder:` block Home Assistant keeps 10 days; leave the
`purge_keep_days` line out rather than keep nine times as much history.
Excluding cameras loses nothing: their history is only "idle". The photos and
Photo Time are unaffected.

## Then set up the alerts

This add-on delivers the data. The alerting — the notification script and the
twenty-five automations — is pasted into the Home Assistant UI and is not part of
the add-on.

Follow `README.md` in the repository, section **New install**, from step 3.

Step 3 is the notification test. Do not skip it. Skipping it is how ten dead
automations went unnoticed on the farm this was built for, until the farm hit
81°F and nobody was told.

## Troubleshooting

**Every entity keeps going unavailable / database growing fast** — two bridges
on one MQTT broker. Usually the retired Windows bridge, still started by Task
Scheduler on the farm PC, alongside this app. Bridges before 1.5.1 all used one
client ID, and the broker allows one connection per ID, so each kicked the
other off about once a second; every kick took every entity unavailable. On the
farm this was built for, the database grew to 16.5 GB at ~10 million rows a day.

Confirm it, any one of:

- **Mosquitto broker** app → **Log**: the same line every second or so —
  `Client farm-bridge already connected, closing old connection.`
- **Duplicate Bridge Detected** is on. Its attributes say why: `other_host`
  names the computer the other bridge runs on (blank if it never said),
  `reason` is `instance` (the other bridge announced itself),
  `foreign_offline` (the other bridge stopped and its Last Will set the farm
  offline — the app sets it straight back online), `foreign_online` (a bridge
  older than 1.5.1 connected: it set the farm online and never announced
  itself) or `disconnects` (over 5 unexpected disconnects in 60 s). When one of the two bridges is this app,
  only the app writes the sensor, so `other_host` is the other computer. The
  automation `25-duplicate-bridge` sends a critical alert when it turns on.
- The **Database health check** below shows thousands of rows an hour for
  every entity.

Fix: stop the other bridge. On a Windows PC, double-click
`UNINSTALL-FARM-BRIDGE.bat` in the repository's `legacy/windows-installer/`
(it lists what it removed), then check **Task Scheduler → Task Scheduler
Library** for any task that still starts `farm_bridge.py` — `Greenery S Farm
Bridge` or `FarmBridge`. On Linux: `sudo systemctl disable --now farm-bridge`.
The sensor clears 10 minutes after the last sign of the other bridge; after
`foreign_online`, 10 minutes after that bridge stops.

Since 1.5.1 each bridge connects with its own client ID
(`greenery-bridge-<farm>-<random>`, in the Log), so two bridges no longer kick
each other off. Run together they are still a fault: every button and switch
press from Home Assistant reaches the farm twice. A bridge older than 1.5.1
never announces itself: this app sees it about 15 s after it connects
(`foreign_online`) and holds the sensor on until it stops. It cannot see one
that was already connected when this app started, nor one that reconnects in
the same few seconds as this app after a broker restart.

**"No MQTT broker found"** — install and *start* the Mosquitto broker add-on.
Installing without starting is the usual cause.

**"Cannot reach the farm"** — Home Assistant must be on the same network as the
hub controller. Check `farm_host`, and confirm the controller is powered.

**Entities appear then go stale** — that is what the module-offline sensors are
for. Check `Any Module Offline` in the device. A module can keep reporting
`connected` while its data stops advancing, and every reading downstream will
look plausible and mean nothing.

**`[WARNING] Output board 244CAB0FC00C missing from frame`** — a data frame
arrived without the relay (output) module. No pump command can be confirmed
without it, so the pump switches grey out at once, and a Pump switch or
Cool-Down press is refused (`REFUSED - no recent data from the output board`)
until the module reports again.

- **A burst of these lines** — a real Output Module outage. Check the module's
  power and cabling, starting with the breakers in the high-voltage box on the
  exterior back. **Output Module Online** goes off, and
  `16-module-offline-critical` sends a critical alert after 5 minutes.
- **Single lines, one at a time, repeating on their own** — flicker: the
  switches grey out briefly with no reconnect behind it. If it happens
  regularly, give this case the same 30-second wait a reconnect gets: in
  `farm_bridge.py`, `publish_binary_states()` stops calling
  `output_board_lost()` when the board is missing, and the 30-second rule then
  covers it.
- **On every frame, from the very first start** — not an outage. The ID is
  fixed in `farm_bridge.py` (`OUTPUT_DEVICE_ID`, this repository's farm), and
  the line always prints it. A farm whose Output Module has another ID never
  matches it: that farm's device map has not been set up.

## Database health check

How many rows each entity wrote to Home Assistant's database in the last hour:
the quickest way to see whether something is flooding the recorder. Run it in
the Terminal (the **Advanced SSH & Web Terminal** app). The query goes in a
file because inline shell quoting breaks in the HA terminal.

1. Save this as `/config/dbcheck4.sql` (File editor or Studio Code Server app):

   ```
   SELECT (SELECT entity_id FROM states_meta m WHERE m.metadata_id = t.metadata_id), t.c FROM (SELECT metadata_id, COUNT(*) AS c FROM states WHERE last_updated_ts > strftime('%s','now','-1 hour') GROUP BY metadata_id) t ORDER BY t.c DESC LIMIT 15;
   ```

2. Run, in the Terminal:

   ```
   apk add sqlite
   mkdir -p /config/sqltmp
   SQLITE_TMPDIR=/config/sqltmp sqlite3 -readonly /config/home-assistant_v2.db < /config/dbcheck4.sql > /config/dbcheck4.txt
   ```

   `-readonly` never changes the database. `SQLITE_TMPDIR` puts sqlite's
   scratch files on the data disk, next to the database.

3. Open `/config/dbcheck4.txt`: the 15 busiest entities, `entity_id|rows`.

**Normal:** a few hundred rows an hour per sensor at most. **Thousands for
every entity**, sensors and switches alike, means their availability is
flapping — see *Every entity keeps going unavailable* under Troubleshooting.
One entity far above the rest is that entity's own problem.

## Credit

Built for a 40 ft Greenery S running at 3 Corners Farm. Shared because the
Greenery S has no supported local monitoring and every operator hits the same
wall.
