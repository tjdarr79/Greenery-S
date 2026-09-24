# Changelog

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
