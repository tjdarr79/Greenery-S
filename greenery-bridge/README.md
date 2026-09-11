# Greenery S Farm Bridge

Reads your Freight Farms Greenery S local Farmhand endpoint and publishes
everything to Home Assistant over MQTT: sensors, all 32 relay channels, module
health, and one-tap Task Mode controls.

No farmhand cloud subscription is needed for any of it.

## Install

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
2. Add `https://github.com/tjdarr79/Greenery-S`
3. Install **Greenery S Farm Bridge**
4. Press **Start**

That is usually the whole setup. If the Mosquitto broker add-on is installed
and running, this add-on picks up the broker address and credentials from
Home Assistant automatically — there is nothing to type.

## Configuration

| Option | Default | |
|---|---|---|
| `farm_host` | `192.168.200.200` | The Farmhand hub controller's IP |
| `farm_port` | `3001` | Local Farmhand port |
| `log_level` | `info` | Raise to `debug` when troubleshooting |
| `mqtt_host` | *(blank)* | Only for an external broker |
| `mqtt_port` | `0` | Blank/0 means 1883 |
| `mqtt_user` | *(blank)* | Only for an external broker |
| `mqtt_password` | *(blank)* | Only for an external broker |

**Leave every `mqtt_*` field blank** unless you run a broker outside Home
Assistant. Filling them in overrides the automatic detection.

Only `farm_host` normally needs attention, and only if your farm controller
does not use the stock address.

## Checking it worked

**Settings → Devices & Services → MQTT → Greenery S Farm.**

You should see roughly 54 entities. Note the device lives *inside* the MQTT
card — it is not listed at the top level of Devices & Services, which is easy
to miss.

The add-on **Log** tab should show `MQTT connected` and then a steady stream of
published states.

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
