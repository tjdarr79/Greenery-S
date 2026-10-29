"""
Read-only check of the four dosing tank level sensors, as farmhand reports them.

Reads the farm's live data stream (/farm-data) and prints module
E831CDC7E680's four digital inputs, one line per reading, then how often each
read 1 and how often it changed. Sends nothing to the farm; changes nothing.

Usage (on hydro, farm LAN):
    py -3 dosing_levels.py               10 readings, one a second
    py -3 dosing_levels.py 60            60 readings
    py -3 dosing_levels.py 60 5          60 readings, one every 5 s
Ctrl+C stops early and still prints the summary.

Reading the result (owner tests, 2026-10-29):
    0 = ok. Low is expected to read 1 - compare with farmhand's Environmental
    card the first time a tank runs low.
    A sensor that flips between 0 and 1 while its tank is full has a corroded
    connector: clean it and run this again.
    A dead or unplugged sensor reads a steady 0 - ok - exactly like a full
    tank. Neither this script nor farmhand can tell the two apart.
"""
import json
import sys
import time
import urllib.request

HOST = "192.168.200.200"
PORT = 3001
MODULE = "E831CDC7E680"
TANKS = (
    ("Nutrient A (Form)", "digital_1"),
    ("Nutrient B (Grow)", "digital_2"),
    ("pH Down", "digital_3"),
    ("Boost / pH Up", "digital_4"),
)
STREAM_TIMEOUT = 90     # farmhand ends /farm-data about every 60 s; we reconnect


def frames():
    """Each /farm-data frame, reconnecting whenever farmhand ends the stream."""
    url = f"http://{HOST}:{PORT}/farm-data"
    while True:
        try:
            req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=STREAM_TIMEOUT) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        yield json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            print(f"       (stream dropped: {e} - reconnecting)")
            time.sleep(2)


def summary(rows):
    print()
    if not rows:
        print("No readings.")
        return
    print(f"{len(rows)} readings:")
    for i, (name, key) in enumerate(TANKS):
        values = [r[i] for r in rows if r[i] is not None]
        if not values:
            print(f"  {name:<18} {key}: never present")
            continue
        ones = sum(1 for v in values if v == 1)
        changes = sum(1 for a, b in zip(values, values[1:]) if a != b)
        if changes:
            verdict = f"FLICKER - {changes} changes: check the connector"
        elif ones:
            verdict = "steady 1 - LOW, if farmhand also shows it low"
        else:
            verdict = "steady 0 - ok (or a dead / unplugged sensor)"
        print(f"  {name:<18} {key}: 1 on {ones} of {len(values)}  {verdict}")


def main():
    readings = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    every = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    print(f"Reading module {MODULE} from http://{HOST}:{PORT}/farm-data - "
          f"{readings} readings, one every {every:g} s. Read-only.\n")
    print("    #  time      " + "  ".join(f"{key:>9}" for _name, key in TANKS))

    rows, next_at, shown_fields = [], 0.0, False
    try:
        for frame in frames():
            if time.monotonic() < next_at:
                continue
            dev = (frame.get("state") or {}).get(MODULE) if isinstance(frame, dict) else None
            if not isinstance(dev, dict):
                print("       module missing from this frame")
                next_at = time.monotonic() + every
                continue
            inputs = dev.get("state") or {}
            if not shown_fields:
                print(f"       (module fields: {json.dumps(inputs, sort_keys=True)})")
                shown_fields = True
            row = [inputs.get(key) for _name, key in TANKS]
            rows.append(row)
            note = "  module reports connected: false" if dev.get("connected") is False else ""
            print(f"  {len(rows):>3}  {time.strftime('%H:%M:%S')}  "
                  + "  ".join(f"{str(v):>9}" for v in row) + note)
            next_at = time.monotonic() + every
            if len(rows) >= readings:
                break
    except KeyboardInterrupt:
        print("\nStopped.")
    summary(rows)


if __name__ == "__main__":
    main()
