# tools/farmhand/

Scripts that talk to farmhand directly, run by hand from a PC on the farm
network (written for the owner's hydro PC: `py -3 <script>`).
Standard library only. **Not needed to run the bridge.**

`dump_settings.py` and `set_24hr.py` send to `POST /farm-control` and read the
answers on farmhand's second stream, `/farm-monitoring` — the HTTP reply is
always `Control message received!` and proves nothing. `dosing_levels.py` only
reads the data stream, `/farm-data`. All three have the Hub address
(`HOST = "192.168.200.200"`, port `3001`) at the top of the file; change it
there for a farm on another address.

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

## dosing_levels.py — read-only

Prints the four dosing tank level inputs (module `E831CDC7E680`,
`digital_1`–`digital_4`) as farmhand reports them, one line per reading, then
how often each read 1 and changed.

```
py -3 dosing_levels.py               10 readings, one a second
py -3 dosing_levels.py 60 5          60 readings, one every 5 s
```

Sends nothing. 0 = ok; low is expected to read 1. A tank that flips between
0 and 1 while full has a corroded connector. A dead or unplugged sensor reads
a steady 0 — the same as a full tank — so a clean run proves the wiring is
quiet, not that the sensor works. Use it to compare with farmhand's
Environmental card, to find a swapped wire, and before and after cleaning a
connector. The bridge's own handling is in `greenery-bridge/DOCS.md`,
*Dosing tank levels*.

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
