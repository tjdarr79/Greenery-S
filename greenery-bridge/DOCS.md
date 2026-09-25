# Greenery S Farm Bridge

Reads your Freight Farms Greenery S local Farmhand endpoint and publishes
everything to Home Assistant over MQTT: sensors, all 32 relay channels, module
health, one-tap Task Mode controls, and pump switches that work only in Task
Mode.

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
| `log_farm_monitoring` | `false` | Investigation only — see [Investigating /farm-monitoring](#investigating-farm-monitoring) |
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

You should see roughly 57 entities. Note the device lives *inside* the MQTT
card — it is not listed at the top level of Devices & Services, which is easy
to miss.

The add-on **Log** tab should show `Farm name: …` with the entity ID prefix,
then `MQTT connected`, then a steady stream of published states.

## Pump switches (Task Mode only)

Three switches turn single pumps on and off from Home Assistant:

| Switch | Relay channel |
|---|---|
| Cultivation Recirc Pump Switch | 1 |
| Left Send Pump Switch | 2 |
| Right Send Pump Switch | 3 |

**They only work in Task Mode.** Outside it they show *unavailable*, and a
command sent to one anyway — by an automation, or published by hand — is
refused: **Farm Control Status** reads `REFUSED - farm not in Task Mode` and
nothing reaches the farm. The bridge checks Task Mode again at the moment of
every command, from output-board data no older than 30 seconds.

**farmhand's reply proves nothing.** It answers `Control message received!` to
every command, including ones it ignores. So the bridge watches the relay's own
state in the farm's data stream and reports `CONFIRMED in 2.1s`, or
`NOT CONFIRMED - relay still reports OFF` after 10 seconds.

A switch left on keeps its pump running until someone turns it off. In Task
Mode farmhand's recipe is suspended, and with it its automatic protections —
stay with the farm while a pump you started is running.

Nothing is sent when the app starts or restarts, and a command left
*retained* on the broker is ignored and cleared rather than replayed.

**Adding a channel** is a code change, in this order: switch it on and off from
the farmhand UI in Task Mode and watch the equipment respond; then add the
channel number to `MANUAL_CONTROL_CHANNELS` in `farm_bridge.py`; then, in Task
Mode, switch it on and off from Home Assistant, watch the equipment, and see
`CONFIRMED` both ways before relying on it. Never channel 23 — the bridge
refuses to start with it listed.

The dashboard card is at the end of `dashboard-controls.yaml`; every tap asks
for confirmation first.

## Investigating /farm-monitoring

`log_farm_monitoring` is for finding out where farmhand answers read-only
commands such as `get_current_mode`. When on, the app logs the first 20 events
of the farm's second data stream, `/farm-monitoring`, after each start, then
disconnects. It sends nothing to the farm and publishes nothing.

1. Turn it on, **Save**, restart the app.
2. Within ten minutes, make farmhand answer something — open the farmhand UI,
   or send `{"command":"get_current_mode"}` to `/farm-control`.
3. Read the **Log** tab for `farm-monitoring event` lines, then turn it off.

## Then set up the alerts

This add-on delivers the data. The alerting — the notification script and the
twenty automations — is pasted into the Home Assistant UI and is not part of
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

## Credit

Built for a 40 ft Greenery S running at 3 Corners Farm. Shared because the
Greenery S has no supported local monitoring and every operator hits the same
wall.
