# Changelog

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
