# tools/farmhand/

Scripts that talk to farmhand's own command channel, run by hand from a PC on
the farm network (written for the owner's hydro PC: `py -3 <script>`).
Standard library only. **Not needed to run the bridge.** All read-only except
`set_24hr.py`.

| Tool | What it does | Sends to farmhand |
|---|---|---|
| `dump_settings.py` | Saves farmhand's live settings, recipes, version, mode, modules | read-only commands farmhand's page sends on load |
| `dump_cameras.py` | Saves the latest photo from each camera plus the raw messages | `read_cameras`, `get_last_images_local_only` (read-only) |
| `dosing_levels.py [N]` | Prints the four dosing tank level inputs, N readings | nothing (listens only) |
| `hvac_logger.py` | Logs HVAC calls, relays, temp, RH every 30 s to CSV | `get_farm_info` (read-only) |
| `set_24hr.py status\|top\|bottom\|nursery\|water on\|off` | Reads or switches the 24-hour nursery light / watering settings | `bulk_update_settings` with ONLY the six `*_24hr_enable` keys; asks for "yes" first |

Commands go to `POST /farm-control`; answers come back on farmhand's second
stream, `/farm-monitoring` (photos on a third, `/farm-images`) — the HTTP reply
is always `Control message received!` and proves nothing. Each script has the
Hub address (`HOST = "192.168.200.200"`, port `3001`) at the top of the file;
change it there for a farm on another address.

Output goes to `results/`, which is gitignored: captures can contain farm
photos and ids. Never commit them — `tests/fixtures/farmhand/` holds the
cleaned-up copies the tests use.

## dump_settings.py — read-only

Saves farmhand's settings and recipe as the Hub holds them, to
`results/farmhand_settings_<date-time>.json` (gitignored — it is one farm's
data).

```
py -3 dump_settings.py
```

Sends only what farmhand's own page sends on every load: `read_settings_files`,
`get_farm_info`, `get_version`, `get_current_mode`, `read_modules`. Changes
nothing. Prints any field that looks like a 24-hour or day-only setting.

Use it to see a setting's real key and value before anyone writes it, and to
take a before/after record around a change made in farmhand Local.

## set_24hr.py — WRITES farm settings

Switches farmhand's 24-hour flags without the cloud app: nursery lights that
stay on through the night, and cultivation watering that keeps cycling at night.

```
py -3 set_24hr.py status
py -3 set_24hr.py top on          (or off)   nursery TOP trough lights
py -3 set_24hr.py bottom on       (or off)   nursery BOTTOM trough lights
py -3 set_24hr.py nursery on      (or off)   all four nursery LED flags
py -3 set_24hr.py water on        (or off)   left + right cultivation watering
```

`status` is read-only. A change shows the before values, asks you to type
`yes`, sends `{"command":"bulk_update_settings","settings":{...}}` — a partial
update, only the keys sent change — then reads the settings back and prints
`CONFIRMED` or `NOT CONFIRMED`.

It can only ever send the six 24-hour keys. The bridge's **24-hour switches**
do the same thing from Home Assistant, with the same six keys hard-coded as
its only writable settings; `tools/verify-repo.py` fails if the two lists ever
differ. Use this script when Home Assistant or the bridge is down.
