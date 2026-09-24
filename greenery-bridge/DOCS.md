# Greenery S Farm Bridge

Reads your Freight Farms Greenery S local Farmhand endpoint and publishes
everything to Home Assistant over MQTT: sensors, all 32 relay channels, module
health, and one-tap Task Mode controls.

No farmhand cloud subscription is needed for any of it.

## Install

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
2. Add `https://github.com/tjdarr79/Greenery-S`
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

## Checking it worked

**Settings → Devices & Services → MQTT → your farm name** (`Greenery S Farm`
unless you changed it).

You should see roughly 54 entities. Note the device lives *inside* the MQTT
card — it is not listed at the top level of Devices & Services, which is easy
to miss.

The add-on **Log** tab should show `Farm name: …` with the entity ID prefix,
then `MQTT connected`, then a steady stream of published states.

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
