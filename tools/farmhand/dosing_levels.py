"""
Read-only: show the dosing tank level sensors as farmhand reports them.

farmhand's module map (from the 2026-10-15 settings dump):
    module E831CDC7E680 (dosing module)
      digital_1 = Nutrient A level  (tank 1, Form)
      digital_2 = Nutrient B level  (tank 2, Grow)
      digital_3 = pH down level     (tank 3, pH)
      digital_4 = Boost / pH up     (tank 4, Boost)

Listens to farmhand's live data stream only. Sends nothing.

Usage (on hydro, farm LAN):
    py -3 dosing_levels.py        (5 readings)
    py -3 dosing_levels.py 30     (30 readings, about 5 min)
Compare what it prints with how full each tank actually is, to learn which
value means "low".
"""
import json
import socket
import time
import urllib.request
from pathlib import Path

HOST = "192.168.200.200"
PORT = 3001
MODULE = "E831CDC7E680"
NAMES = {"digital_1": "Tank 1 Form ", "digital_2": "Tank 2 Grow ",
         "digital_3": "Tank 3 pH   ", "digital_4": "Tank 4 Boost"}
import sys
FRAMES = int(sys.argv[1]) if len(sys.argv) > 1 else 5


def frames():
    """Yield data frames; reconnect when farmhand drops the stream (about every 60 s)."""
    while True:
        try:
            req = urllib.request.Request(f"http://{HOST}:{PORT}/farm-data",
                                         headers={"Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                buf = []
                while True:
                    try:
                        line = resp.readline()
                    except (socket.timeout, TimeoutError):
                        continue
                    if not line:
                        break
                    line = line.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        buf.append(line[5:].strip())
                    elif line == "" and buf:
                        try:
                            msg = json.loads("\n".join(buf))
                        except json.JSONDecodeError:
                            msg = None
                        buf = []
                        if isinstance(msg, dict) and "state" in msg:
                            yield msg
        except OSError:
            pass
        time.sleep(2)


OUT = Path(__file__).parent / "results" / "dosing_levels.txt"
_lines = []
_print = print


def print(*a):  # also keep a copy for results\\dosing_levels.txt
    s = " ".join(str(x) for x in a)
    _lines.append(s)
    _print(s)


def main():
    n = 0
    for msg in frames():
        mod = (msg.get("state") or {}).get(MODULE)
        if mod is None:
            print(f"Module {MODULE} not in this frame. Modules seen: {list(msg['state'])}")
        else:
            st = mod.get("state") if isinstance(mod, dict) else None
            st = st if isinstance(st, dict) else mod
            vals = "  ".join(f"{NAMES[io].strip()}={st.get(io, '?')}" for io in NAMES)
            print(f"{time.strftime('%H:%M:%S')}   {vals}")
        n += 1
        if n >= FRAMES:
            break
        time.sleep(2)
    print("\nDone. Tell Claude which tanks are actually low or full right now.")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(_lines), encoding="utf-8")
    _print(f"Saved: {OUT}")


if __name__ == "__main__":
    main()
