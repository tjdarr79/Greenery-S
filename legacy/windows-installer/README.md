# Retired: Windows installer

> **Do not run alongside the Home Assistant app. Two bridges on one broker
> flood the database (see CHANGELOG 1.5.1).** To remove this bridge from a PC:
> double-click `UNINSTALL-FARM-BRIDGE.bat` in `legacy/windows-installer/` (it
> removes the startup task, stops the bridge and lists what it did), then open
> **Task Scheduler → Task Scheduler Library** and check that no task still
> starts `farm_bridge.py` — for example `FarmBridge`, from the manual steps in
> `WINDOWS-INSTALL.md`.

**Do not use this for a new install.** Since 2026-09-24 the bridge runs as a Home
Assistant app, which needs no separate PC, no Python and no typed MQTT
credentials — follow **New install** in the [main README](../../README.md#new-install--do-these-in-order)
and [`greenery-bridge/DOCS.md`](../../greenery-bridge/DOCS.md). Everything here —
`INSTALL-FARM-BRIDGE.bat`, `UNINSTALL-FARM-BRIDGE.bat`, `install/` and the manual
`WINDOWS-INSTALL.md` — is the previous way of running the bridge on a Windows PC,
kept only for reference and as an emergency fallback if Home Assistant cannot host
the app. It is not maintained, and it installs whatever `farm_bridge.py` is at the
repo root, so run it from a checkout of `stable`, not `main`.
