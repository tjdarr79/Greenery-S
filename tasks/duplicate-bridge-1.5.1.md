# Task: Duplicate Bridge Protection (target 1.5.1)

**Priority: blocks `stable`.** Found 2026-10-30 on 3 Corners Farm.
Read `CLAUDE.md` first. Work on `main` (or a branch from it), never `stable`.

## What happened (evidence)

- The Home Assistant database grew to **16.5 GB**; free space fell to 3.0 GB of 28 GB.
- Normal days: ~60,000 state rows. Flood days: **~10 million rows/day** on
  Sep 30 – Oct 3, Oct 12 – 18 and Oct 29 – 30.
- During the flood, **every** entity (sensors, cameras, Relay State Mismatch,
  Farm Control Status) wrote ~6,700 rows/hour: one change every ~0.5 s. So
  every entity was flapping unavailable ↔ available about once per second.
- Side effect: Home Assistant was overloaded and Cool-Down became very slow.

## Root cause

The legacy Windows bridge (installed earlier with
`legacy/windows-installer/`, running `farm_bridge.py` from a **scheduled task
at PC startup**) was still running on the farm PC. It and the HA app
both connect to Mosquitto with **`client_id="farm-bridge"`**
(`build_mqtt_client()` in `farm_bridge.py`). The broker allows one connection
per client ID, so each connection kicked the other off. paho reconnected
within a second, the Last Will set `farm/bridge/status` = `offline` on every
kick and `online` on every reconnect, and the availability of every entity
flapped. The floods started each time the farm PC restarted and stopped when
it was shut down or the task was ended.

The owner has deleted the scheduled task. The bridge must make this
impossible to miss next time, on any farm.

## Requirements

### 1. Unique MQTT client ID
- `client_id = f"greenery-bridge-{farm_slug(FARM_NAME)}-{secrets.token_hex(3)}"`,
  generated once per process start. Log it at INFO on connect.
- Two bridges can then never kick each other off.
- Do **not** change any MQTT topic, unique_id or the device identifier
  (CLAUDE.md). The client ID is not a topic.

### 2. Detect a second bridge
Three independent signals; any one turns the alarm on.

a. **Instance topic.** On every connect, publish retained
   `{MQTT_BASE_TOPIC}/bridge/instance` =
   `{"id": "<client_id>", "host": "<hostname>", "version": "<app version>", "started": <epoch>}`
   and subscribe to it. If a message arrives with an `id` that is not ours,
   another bridge is publishing for this farm:
   - log ERROR once per 10 min:
     `Another Greenery bridge is running on this broker: id=..., host=..., version=...`
   - re-publish our own instance message, at most once per 60 s (no ping-pong).
b. **Foreign "offline".** If `{MQTT_BASE_TOPIC}/bridge/status` arrives as
   `offline` while we are connected, another client's Last Will fired (an old
   bridge died). Re-publish `online` (retained) immediately so entities don't
   stay unavailable, and raise the alarm with reason `foreign_offline`.
c. **Disconnect rate.** Count unexpected disconnects (`rc != 0`). More than 5
   in 60 s → same alarm. This catches an OLD bridge that still uses
   `client_id="farm-bridge"` and never publishes an instance message.

Alarm entity: `binary_sensor` **Duplicate Bridge Detected**,
`device_class: problem`, `entity_category: diagnostic`, retained, with
attributes `other_id`, `other_host`, `other_version`, `reason`
(`instance` / `foreign_offline` / `disconnects`). It clears after 10 min with no foreign
instance message and no more than 1 unexpected disconnect.

### 3. Alert automation
New `automations/25-duplicate-bridge.yaml`, alias **Farm Duplicate Bridge**:
- Trigger: the binary sensor goes off → on.
- `script.farm_alerts`, **critical: true**. Message names the other host if
  known: "Another Greenery bridge is connected to this farm's broker (host X).
  Every sensor is flapping and Home Assistant's database is filling fast.
  Stop the other bridge: on a Windows PC check Task Scheduler for the farm
  bridge task."
- Fires once per off→on; never on restart (use `from: "off"`).

### 4. Legacy Windows installer
- `legacy/windows-installer/README.md` and `WINDOWS-INSTALL.md`: a warning at
  the top: **"Do not run alongside the Home Assistant app. Two bridges on one
  broker flood the database (see CHANGELOG 1.5.1)."** And how to remove it:
  run `UNINSTALL-FARM-BRIDGE.bat`, then check Task Scheduler.
- `Uninstall-FarmBridge.ps1`: make sure it removes the scheduled task the
  installer creates (check the exact task name the installer registers) and
  stops any running `farm_bridge.py` python process. Report what it did.

### 5. Docs
- `greenery-bridge/DOCS.md`, Troubleshooting: **"Every entity keeps going
  unavailable / database growing fast"**. Cause: two bridges on one broker.
  How to confirm: Mosquitto log lines `Client ... already connected, closing
  old connection`, the Duplicate Bridge Detected sensor, its `other_host`.
  Fix: stop the other bridge.
- Add a **database health check** section: the last-hour rows-per-entity
  query, run from a `.sql` file (inline shell quoting breaks in the HA
  terminal):
  ```
  apk add sqlite
  SQLITE_TMPDIR=/config/sqltmp sqlite3 -readonly /config/home-assistant_v2.db < /config/dbcheck4.sql > /config/dbcheck4.txt
  ```
  ```
  SELECT (SELECT entity_id FROM states_meta m WHERE m.metadata_id = t.metadata_id), t.c FROM (SELECT metadata_id, COUNT(*) AS c FROM states WHERE last_updated_ts > strftime('%s','now','-1 hour') GROUP BY metadata_id) t ORDER BY t.c DESC LIMIT 15;
  ```
  Normal: a few hundred rows/hour per sensor at most. Thousands for every
  entity = flapping availability.
- `README.md` install checklist: **"No other bridge on this farm's network"**
  (check for a Windows farm bridge task; check the Mosquitto log after install).
- `CHANGELOG.md` 1.5.1 entry; bump `version:` in `greenery-bridge/config.yaml`.

### 6. Copies and checks
- Edit the root `farm_bridge.py`, copy it over `greenery-bridge/farm_bridge.py`.
- `python tools/verify-repo.py` → `ALL CHECKS PASSED`. Add a check that
  `client_id="farm-bridge"` (a fixed client ID) never reappears.

## Tests (unit)
1. Two calls to the client-ID builder in separate runs give different IDs,
   both starting `greenery-bridge-<slug>-`.
2. A foreign instance message turns Duplicate Bridge Detected on, with
   `other_host`; our own message doesn't.
3. A retained `offline` on bridge/status while connected: `online` re-published at once, alarm on with reason foreign_offline.
4. Six unexpected disconnects in 60 s turn it on; one per minute doesn't.
5. Clears after 10 quiet minutes.
6. Re-publish of our instance message is limited to once per 60 s.

## Acceptance (owner)
1. After the update: bridge log shows the new client ID; Duplicate Bridge
   Detected is **off**.
2. Last-hour rows per entity: a few hundred at most.
3. **Duplicate test (short, supervised):** on the farm PC, run the NEW
   `farm_bridge.py` from the repo for 2 minutes (with a farm-bridge.env
   pointing at the HA broker), then stop it with Ctrl+C. Expected:
   Duplicate Bridge Detected on within about 1 minute with `other_host` = the
   PC, one critical alert, entities do NOT flap (unique IDs, no kicking).
   After stopping it, entities stay available (foreign-offline re-publish),
   and the sensor clears within 10 minutes. Do not run the OLD
   `C:\Farm\farm_bridge.py.retired` for this test: it still uses the fixed
   client ID.
4. Restart the bridge and HA: no duplicate alert.
5. `verify-repo.py` passes; summarize every file changed.
