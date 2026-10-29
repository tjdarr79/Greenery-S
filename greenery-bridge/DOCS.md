# Greenery S Farm Bridge

Reads your Freight Farms Greenery S local Farmhand endpoint and publishes
everything to Home Assistant over MQTT: sensors, all 32 relay channels, module
health, one-tap Task Mode controls, pump switches that work only in Task Mode,
a one-tap Cool-Down, switches for farmhand's 24-hour settings, and the
latest photo from each of the farm's six cameras.

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

You should see roughly 85 entities. Note the device lives *inside* the MQTT
card — it is not listed at the top level of Devices & Services, which is easy
to miss.

The add-on **Log** tab should show `Farm name: …` with the entity ID prefix,
then `MQTT connected`, then a steady stream of published states.

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
  reads from farmhand. It lists those cameras, and `23-camera-stale` sends one
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
twenty-three automations — is pasted into the Home Assistant UI and is not part of
the add-on.

Follow `README.md` in the repository, section **New install**, from step 3.

Step 3 is the notification test. Do not skip it. Skipping it is how ten dead
automations went unnoticed on the farm this was built for, until the farm hit
81°F and nobody was told.

## Troubleshooting

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

## Credit

Built for a 40 ft Greenery S running at 3 Corners Farm. Shared because the
Greenery S has no supported local monitoring and every operator hits the same
wall.
